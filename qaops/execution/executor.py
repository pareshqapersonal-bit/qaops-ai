"""Adaptive execution: model failover, then provider failover (ADR-026, ADR-027).

Runs a pipeline stage by stage, checkpointing each success. On failure the
error is classified and policy decides the response - and the response is now
model-aware: a failure specific to one model (its credit exhausted, it went
unavailable, the request overflowed its context) tries a sibling model on the
*same* provider before the provider is abandoned. Only when every compatible
model on a provider has failed does execution move to the next provider.

That ordering matters in practice: a provider whose credentials are good and
whose other models are affordable should not be discarded because one model ran
out of budget.

Switching model or provider works by rebuilding the remaining stages with a
client configured for the new target. Stages take their client in `__init__`
and hold it, so they cannot be handed a new one mid-flight - but constructing
them afresh is cheap, completed outputs are already checkpointed, and every
stage stays completely unaware that anything changed.
"""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.core.protocols import PipelineStage
from qaops.execution.events import EventType, ExecutionEvent
from qaops.execution.models import ModelHealth, ModelInfo, ModelRegistry
from qaops.execution.policy import Action, FailureKind, Recovery, recovery_for
from qaops.execution.registry import ProviderHealth, ProviderInfo
from qaops.execution.selector import StageRequirements, select_candidates
from qaops.llm.request_budget import RequestBudgetExhausted, observing


class _Named:
    """Minimal name-carrier so _emit can take a bare provider/model string.

    The observer knows provider and model only as strings (that is all the
    client exposes), while _emit otherwise takes ProviderInfo/ModelInfo. This
    tiny adapter lets both paths share one emit method without duplicating it.
    """

    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name = name


_NamedProvider = _Named
_NamedModel = _Named

logger = logging.getLogger(__name__)

# Builds the pipeline stages for one provider/model pairing. Called again on
# every switch, so stage construction stays outside this module entirely.
StageFactory = Callable[[QAOpsSettings], Sequence[PipelineStage[BaseModel, BaseModel]]]

Reporter = Callable[[str], None]
EventSink = Callable[[ExecutionEvent], None]

# Settings field holding the model name, per provider.
_MODEL_FIELD: dict[str, str] = {
    "anthropic": "model",
    "gemini": "gemini_model",
    "openrouter": "openrouter_model",
}


@dataclass
class StageCheckpoint:
    """The output of one completed stage, and what produced it."""

    stage_name: str
    provider: str
    model: str
    output: BaseModel


@dataclass
class ExecutionReport:
    """What happened during a run, for user feedback and tests."""

    checkpoints: list[StageCheckpoint] = field(default_factory=list)
    provider_switches: list[tuple[str, str, str]] = field(default_factory=list)
    model_switches: list[tuple[str, str, str]] = field(default_factory=list)
    health: dict[str, ProviderHealth] = field(default_factory=dict)
    model_health: dict[str, ModelHealth] = field(default_factory=dict)

    @property
    def completed_stages(self) -> list[str]:
        return [checkpoint.stage_name for checkpoint in self.checkpoints]

    @property
    def providers_used(self) -> list[str]:
        seen: list[str] = []
        for checkpoint in self.checkpoints:
            if checkpoint.provider not in seen:
                seen.append(checkpoint.provider)
        return seen

    @property
    def models_used(self) -> list[str]:
        seen: list[str] = []
        for checkpoint in self.checkpoints:
            if checkpoint.model not in seen:
                seen.append(checkpoint.model)
        return seen


class AdaptiveExecutor:
    """Executes stages, exhausting models within a provider before switching."""

    def __init__(
        self,
        providers: Sequence[ProviderInfo],
        settings: QAOpsSettings,
        stage_factory: StageFactory,
        *,
        registry: ModelRegistry | None = None,
        reporter: Reporter | None = None,
        events: EventSink | None = None,
        max_attempts_per_model: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not providers:
            msg = "AdaptiveExecutor requires at least one provider"
            raise ValueError(msg)
        self._providers = list(providers)
        self._settings = settings
        self._stage_factory = stage_factory
        self._registry = registry if registry is not None else ModelRegistry()
        self._report_line = reporter or (lambda _message: None)
        self._emit_event = events or (lambda _event: None)
        self._max_attempts = max_attempts_per_model
        # Bounds from settings (ADR-029). These keep recovery from walking a
        # hundreds-long discovered catalogue.
        self._max_models_per_provider = settings.max_models_per_provider_per_stage
        self._max_stage_recovery = settings.max_stage_recovery_attempts
        self._max_provider_calls = settings.max_provider_calls_per_stage
        self._sleep = sleep
        # Actual provider generation calls made for the CURRENT stage, across all
        # models and structured-output repair attempts. Reset at each stage
        # boundary; enforces max_provider_calls_per_stage (ADR-030).
        self._stage_provider_calls = 0
        self.report = ExecutionReport(
            health={info.name: ProviderHealth(name=info.name) for info in providers}
        )
        # Models already ruled out this run, keyed by provider.
        self._excluded: dict[str, set[str]] = {info.name: set() for info in providers}
        # Distinct models tried on each provider for the CURRENT stage; reset at
        # each stage boundary. Enforces the per-provider-per-stage model cap.
        self._models_tried: dict[str, set[str]] = {info.name: set() for info in providers}

    # --- candidate selection -------------------------------------------------

    def _candidates(self, provider: ProviderInfo) -> list[ModelInfo]:
        """Bounded, ranked, compatible models for a provider (ADR-029).

        The registry may return hundreds of discovered models; the selector
        filters incompatible ones, ranks by capability, and returns at most
        `max_models_per_provider_per_stage`. This is the single choke point that
        prevents the executor from ever iterating the full catalogue.
        """
        models = self._registry.models_for(provider.name)
        if not models:
            # No discovered or curated models (e.g. the mock provider, or a
            # provider whose discovery failed and has no static table). Use the
            # configured model name if set, else a single synthetic candidate so
            # the provider is still executable - one attempt, no model failover.
            configured = self._configured_model(provider.name)
            if provider.name in self._excluded and configured in self._excluded[provider.name]:
                return []
            name = configured or f"{provider.name}-default"
            return [ModelInfo(name=name, provider=provider.name)]
        scored = select_candidates(
            models,
            StageRequirements(),
            limit=self._max_models_per_provider,
            configured=self._configured_model(provider.name) or None,
            excluded=self._excluded[provider.name],
        )
        return [entry.model for entry in scored]

    def _candidates_excluding(self, provider: ProviderInfo, exclude: set[str]) -> list[ModelInfo]:
        """Bounded candidates with an extra per-stage exclusion applied.

        The cap is applied AFTER excluding models already tried this stage, so
        `max_models_per_provider_per_stage` counts distinct models attempted for
        the stage - not the size of each recomputed pool. Once this returns
        empty, the provider's model budget for the stage is spent.
        """
        models = self._registry.models_for(provider.name)
        if not models:
            configured = self._configured_model(provider.name)
            if not configured or configured in exclude:
                return []
            return [ModelInfo(name=configured, provider=provider.name)]
        scored = select_candidates(
            models,
            StageRequirements(),
            limit=self._max_models_per_provider,
            configured=self._configured_model(provider.name) or None,
            excluded=self._excluded[provider.name] | exclude,
        )
        return [entry.model for entry in scored]

    def _configured_model(self, provider: str) -> str:
        field_name = _MODEL_FIELD.get(provider)
        if field_name is None:
            return ""
        return str(getattr(self._settings, field_name, ""))

    def _settings_for(self, provider: ProviderInfo, model: ModelInfo) -> QAOpsSettings:
        update: dict[str, object] = {"provider": provider.name}
        field_name = _MODEL_FIELD.get(provider.name)
        if field_name is not None and model.name:
            update[field_name] = model.name
        return self._settings.model_copy(update=update)

    def _healthy_providers(self) -> list[ProviderInfo]:
        return [p for p in self._providers if self.report.health[p.name].available]

    def _next_provider(self, current: ProviderInfo) -> ProviderInfo | None:
        for candidate in self._healthy_providers():
            if candidate.name != current.name and self._candidates(candidate):
                return candidate
        return None

    def _health_for(self, provider: str, model: str) -> ModelHealth:
        key = f"{provider}/{model}"
        health = self.report.model_health.get(key)
        if health is None:
            health = ModelHealth(name=key)
            self.report.model_health[key] = health
        return health

    # --- execution -----------------------------------------------------------

    def run(self, data: BaseModel) -> BaseModel:
        """Run every stage, adapting model and provider as failures require."""
        provider = self._select_first_provider()
        model = self._candidates(provider)[0]
        stages = list(self._stage_factory(self._settings_for(provider, model)))
        current: BaseModel = data
        index = 0

        while index < len(stages):
            stage = stages[index]
            attempts = 0
            # Models tried for THIS stage, so a retryable failure that switches
            # models cannot cycle back to one already exhausted here. Reset when
            # the stage completes, since a later stage may reuse the model.
            tried_here: set[str] = set()
            # Recovery actions (model/provider switches) spent on THIS stage.
            # Bounds the total across all providers, independent of the per-
            # provider model cap (ADR-029, section 7). Same-model retries do NOT
            # count here - they are bounded separately by max_attempts_per_model.
            recovery_actions = 0
            # Which distinct model this is for the stage, 1-based (ADR-030).
            # Increments on a model switch; request_attempt (= attempts) resets.
            model_attempt_number = 1
            # Reset the per-provider model budget for this stage.
            for tried in self._models_tried.values():
                tried.clear()
            # Reset the per-stage provider-call budget.
            self._stage_provider_calls = 0
            self._emit(EventType.STAGE_STARTED, stage, index, len(stages), provider, model)
            while True:
                attempts += 1
                # Bind an observer for this stage run. stage.run() may make
                # several real provider calls (structured-output repair); the
                # observer counts each one against the per-stage provider-call
                # budget, emits a REQUEST_STARTED/COMPLETED/FAILED pair per real
                # call, and vetoes further calls once the budget is spent. This
                # is the seam that makes hidden calls visible (ADR-030).
                observer = self._StageObserver(
                    executor=self,
                    stage=stage,
                    index=index,
                    count=len(stages),
                    model_attempt_number=model_attempt_number,
                    recovery_attempts=recovery_actions,
                )
                try:
                    with observing(observer):
                        output = stage.run(current)
                except RequestBudgetExhausted as exc:
                    # The per-stage provider-call budget stopped further calls.
                    # This is a clean, deterministic terminal state - not a
                    # schema failure to recover from - so fail the stage.
                    self._emit(
                        EventType.STAGE_FAILED,
                        stage,
                        index,
                        len(stages),
                        provider,
                        model,
                        recovery_attempts=recovery_actions,
                        message="provider-call budget exhausted",
                    )
                    raise StageError(
                        stage.name,
                        f"Provider-call budget exhausted after "
                        f"{self._stage_provider_calls} calls on stage "
                        f"{stage.name!r}. Last model {provider.name}/{model.name}.",
                    ) from exc
                except Exception as exc:  # noqa: BLE001 - classified below
                    recovery = recovery_for(str(exc))
                    self._health_for(provider.name, model.name).record_failure()
                    self.report.health[provider.name].record_failure(recovery.kind.value)
                    self._report_line(
                        f"  {stage.name}: {provider.name}/{model.name} "
                        f"failed ({recovery.kind.value})"
                    )
                    # A timeout gets its own event so the API can show it
                    # distinctly from other failures (section 10).
                    if recovery.kind is FailureKind.TIMEOUT:
                        self._emit(
                            EventType.REQUEST_TIMED_OUT,
                            stage,
                            index,
                            len(stages),
                            provider,
                            model,
                            model_attempt_number=model_attempt_number,
                            request_attempt=attempts,
                            recovery_attempts=recovery_actions,
                            message="Request timed out",
                        )
                    self._emit(
                        EventType.MODEL_FAILED,
                        stage,
                        index,
                        len(stages),
                        provider,
                        model,
                        failure_kind=recovery.kind.value,
                        recovery_attempts=recovery_actions,
                    )
                    logger.warning(
                        "execution.stage_failed stage=%s provider=%s model=%s kind=%s",
                        stage.name,
                        provider.name,
                        model.name,
                        recovery.kind.value,
                    )

                    # A same-model retry is not a recovery action and does not
                    # consume the stage budget.
                    is_retry = (
                        recovery.action in {Action.RETRY_SAME, Action.RETRY_SAME_WITH_BACKOFF}
                        and attempts < self._max_attempts
                    )
                    if not is_retry:
                        recovery_actions += 1
                        if recovery_actions > self._max_stage_recovery:
                            self._emit(
                                EventType.STAGE_FAILED,
                                stage,
                                index,
                                len(stages),
                                provider,
                                model,
                                recovery_attempts=recovery_actions - 1,
                                message="stage recovery budget exhausted",
                            )
                            raise StageError(
                                stage.name,
                                f"Stage recovery budget exhausted after "
                                f"{recovery_actions - 1} recovery actions. "
                                f"Last failure on {provider.name}/{model.name}: "
                                f"{recovery.kind.value}.",
                            ) from exc

                    target = self._plan_recovery(
                        recovery, provider, model, attempts, stage.name, exc, tried_here
                    )
                    if target is None:
                        # Retrying the SAME model: request_attempt will increment
                        # on the next loop; model_attempt_number is unchanged.
                        self._emit(
                            EventType.REQUEST_RETRY,
                            stage,
                            index,
                            len(stages),
                            provider,
                            model,
                            model_attempt_number=model_attempt_number,
                            request_attempt=attempts + 1,
                            recovery_attempts=recovery_actions,
                            message=(
                                "Request timed out; retrying same model"
                                if recovery.kind is FailureKind.TIMEOUT
                                else "Retrying same model"
                            ),
                        )
                        if recovery.backoff_seconds:
                            self._sleep(recovery.backoff_seconds * attempts)
                        continue

                    tried_here.add(model.name)
                    previous_provider = provider.name
                    provider, model = target
                    # A new distinct model for this stage.
                    model_attempt_number += 1
                    if provider.name != previous_provider:
                        self._emit(
                            EventType.PROVIDER_SWITCH,
                            stage,
                            index,
                            len(stages),
                            provider,
                            model,
                            model_attempt_number=model_attempt_number,
                            request_attempt=0,
                            recovery_attempts=recovery_actions,
                        )
                    else:
                        self._emit(
                            EventType.MODEL_SWITCH,
                            stage,
                            index,
                            len(stages),
                            provider,
                            model,
                            model_attempt_number=model_attempt_number,
                            request_attempt=0,
                            models_attempted=len(tried_here),
                            recovery_attempts=recovery_actions,
                            message=recovery.explanation,
                        )
                    stages = list(self._stage_factory(self._settings_for(provider, model)))
                    stage = stages[index]
                    attempts = 0
                    continue

                self.report.checkpoints.append(
                    StageCheckpoint(
                        stage_name=stage.name,
                        provider=provider.name,
                        model=model.name,
                        output=output,
                    )
                )
                self._report_line(f"  {stage.name}: {provider.name}/{model.name} ok")
                self._emit(EventType.STAGE_COMPLETED, stage, index, len(stages), provider, model)
                current = output
                index += 1
                break

        return current

    class _StageObserver:
        """Counts and budgets the real provider calls inside one stage.run().

        A stage may call the provider several times (structured-output repair).
        Each call is announced here, so it is counted against the per-stage
        provider-call budget, made visible as REQUEST_STARTED / REQUEST_COMPLETED
        / REQUEST_FAILED events, and stopped when the budget is spent (ADR-030).
        `provider_call_number` is the running total for the stage, so the API's
        progress reflects actual provider calls rather than executor loops.
        """

        def __init__(
            self,
            *,
            executor: "AdaptiveExecutor",
            stage: PipelineStage[BaseModel, BaseModel],
            index: int,
            count: int,
            model_attempt_number: int,
            recovery_attempts: int,
        ) -> None:
            self._ex = executor
            self._stage = stage
            self._index = index
            self._count = count
            self._model_attempt_number = model_attempt_number
            self._recovery_attempts = recovery_attempts

        def before_request(self, *, provider: str, model: str, attempt: int) -> None:
            # Veto if the stage's provider-call budget is already spent.
            if self._ex._stage_provider_calls >= self._ex._max_provider_calls:
                raise RequestBudgetExhausted(
                    f"provider-call budget of {self._ex._max_provider_calls} "
                    f"reached for stage {self._stage.name!r}"
                )
            self._ex._stage_provider_calls += 1
            self._ex._emit(
                EventType.REQUEST_STARTED,
                self._stage,
                self._index,
                self._count,
                _NamedProvider(provider),
                _NamedModel(model),
                model_attempt_number=self._model_attempt_number,
                request_attempt=attempt,
                provider_call_number=self._ex._stage_provider_calls,
                recovery_attempts=self._recovery_attempts,
                message="Waiting for provider response",
            )

        def after_request(
            self, *, provider: str, model: str, attempt: int, empty: bool, chars: int
        ) -> None:
            self._ex._emit(
                EventType.REQUEST_COMPLETED,
                self._stage,
                self._index,
                self._count,
                _NamedProvider(provider),
                _NamedModel(model),
                model_attempt_number=self._model_attempt_number,
                request_attempt=attempt,
                provider_call_number=self._ex._stage_provider_calls,
                recovery_attempts=self._recovery_attempts,
                message=("empty response" if empty else f"received {chars} chars"),
            )

    def _emit(
        self,
        event_type: EventType,
        stage: PipelineStage[BaseModel, BaseModel],
        index: int,
        count: int,
        provider: "ProviderInfo | _Named | None" = None,
        model: "ModelInfo | _Named | None" = None,
        *,
        model_attempt_number: int = 0,
        request_attempt: int = 0,
        provider_call_number: int = 0,
        models_attempted: int = 0,
        recovery_attempts: int = 0,
        failure_kind: str | None = None,
        message: str = "",
    ) -> None:
        """Emit a structured execution event (ADR-029, ADR-030)."""
        self._emit_event(
            ExecutionEvent(
                type=event_type,
                stage=stage.name,
                stage_index=index,
                stage_count=count,
                provider=provider.name if provider else None,
                model=model.name if model else None,
                model_attempt_number=model_attempt_number,
                request_attempt=request_attempt,
                provider_call_number=provider_call_number,
                models_attempted=models_attempted,
                recovery_attempts=recovery_attempts,
                failure_kind=failure_kind,
                message=message,
            )
        )

    def _select_first_provider(self) -> ProviderInfo:
        for candidate in self._healthy_providers():
            if self._candidates(candidate):
                return candidate
        msg = "No provider has any compatible model available"
        raise StageError("execution", msg)

    def _plan_recovery(
        self,
        recovery: Recovery,
        provider: ProviderInfo,
        model: ModelInfo,
        attempts: int,
        stage_name: str,
        exc: Exception,
        tried_here: set[str],
    ) -> tuple[ProviderInfo, ModelInfo] | None:
        """Decide the next provider/model, or None to retry the current one."""
        if recovery.disables_provider:
            self.report.health[provider.name].mark_unavailable(recovery.explanation)
        elif recovery.disables_model:
            self._excluded[provider.name].add(model.name)
            self._health_for(provider.name, model.name).mark_unavailable(recovery.explanation)

        retrying_same = recovery.action in {
            Action.RETRY_SAME,
            Action.RETRY_SAME_WITH_BACKOFF,
        }
        if retrying_same and attempts < self._max_attempts:
            return None

        if self.report.health[provider.name].available:
            # The per-provider cap counts distinct models tried for this stage
            # on this provider. Once that many have been attempted, the
            # provider's budget is spent even if more discovered models remain -
            # the choke point that stops a hundreds-long catalogue being walked.
            self._models_tried[provider.name].add(model.name)
            budget_left = len(self._models_tried[provider.name]) < self._max_models_per_provider
            if budget_left:
                already = self._excluded[provider.name] | self._models_tried[provider.name]
                siblings = self._candidates_excluding(provider, already)
                if recovery.action is Action.LARGER_CONTEXT_MODEL:
                    siblings = [
                        candidate
                        for candidate in siblings
                        if candidate.max_context_tokens > model.max_context_tokens
                    ]
                if siblings:
                    replacement = siblings[0]
                    self.report.model_switches.append((stage_name, model.name, replacement.name))
                    self._report_line(
                        f"  trying {provider.name}/{replacement.name} ({recovery.explanation})"
                    )
                    return provider, replacement
            self.report.health[provider.name].mark_unavailable(
                f"model budget spent ({self._max_models_per_provider} models tried)"
            )
            self._report_line(
                f"  {provider.name} exhausted after "
                f"{len(self._models_tried[provider.name])} model(s)"
            )

        replacement_provider = self._next_provider(provider)
        if replacement_provider is None:
            raise StageError(
                stage_name,
                f"All providers failed. Last error from {provider.name}/{model.name}: {exc}",
            ) from exc
        replacement_model = self._candidates(replacement_provider)[0]
        self.report.provider_switches.append((stage_name, provider.name, replacement_provider.name))
        self._report_line(
            f"  switching {provider.name} -> {replacement_provider.name}/{replacement_model.name}"
        )
        return replacement_provider, replacement_model
