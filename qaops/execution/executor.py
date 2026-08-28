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
from qaops.execution.candidates import (
    configured_model,
    configured_model_is_free,
    provider_supports_images,
    settings_for_model,
    synthetic_candidate,
)
from qaops.execution.events import EventType, ExecutionEvent
from qaops.execution.models import ModelHealth, ModelInfo, ModelRegistry
from qaops.execution.policy import Action, FailureKind, Recovery, recovery_for_exception
from qaops.execution.registry import ProviderHealth, ProviderInfo
from qaops.execution.selector import StageRequirements, select_candidates
from qaops.execution.strategy import ExecutionStrategy, parse_strategy
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
# (stage_name, stage_index, stage_output_model) -> None. Called after a stage
# completes so the orchestration layer can persist a checkpoint (ADR-040).
CheckpointSink = Callable[[str, int, BaseModel], None]


@dataclass
class StageCheckpoint:
    """The output of one completed stage, and what produced it."""

    stage_name: str
    provider: str
    model: str
    output: BaseModel


@dataclass
class AttemptRecord:
    """One sanitized provider/model attempt that failed, for the attempt history.

    Contains only normalized, non-sensitive fields (ADR-035): stage, provider,
    model, failure kind, and optional sanitized HTTP status / provider error
    code. Never carries keys, headers, request payloads, or raw exception text.
    """

    stage: str
    provider: str
    model: str
    failure_kind: str
    status_code: int | None = None
    error_code: str | None = None
    model_attempt_number: int = 0
    provider_call_number: int = 0


@dataclass
class ExecutionReport:
    """What happened during a run, for user feedback and tests."""

    checkpoints: list[StageCheckpoint] = field(default_factory=list)
    provider_switches: list[tuple[str, str, str]] = field(default_factory=list)
    model_switches: list[tuple[str, str, str]] = field(default_factory=list)
    health: dict[str, ProviderHealth] = field(default_factory=dict)
    model_health: dict[str, ModelHealth] = field(default_factory=dict)
    # Ordered, sanitized history of every failed attempt across all stages. The
    # frontend/API uses this to show the full failover story instead of only the
    # last error (ADR-035).
    attempts: list[AttemptRecord] = field(default_factory=list)

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
        checkpoint: CheckpointSink | None = None,
        start_index: int = 0,
        max_attempts_per_model: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        image_stage_name: str | None = None,
        stage_names: tuple[str, ...] = (),
    ) -> None:
        if not providers:
            msg = "AdaptiveExecutor requires at least one provider"
            raise ValueError(msg)
        self._settings = settings
        # Phase 40B: the name of the single image-consuming stage (the
        # requirement analyzer) when this run carries image evidence, else None.
        # Per-stage selection uses it: the image stage requires an image-capable
        # provider; every downstream stage of an image run EXCLUDES the image
        # provider (NVIDIA) so downstream text stages never touch it. Identified
        # by the orchestration layer (DesignService), not a hard-coded index.
        self._image_stage_name = image_stage_name
        # Ordered stage names for this run (Phase 40B), supplied by the
        # orchestration layer so the executor can set the current stage before
        # selecting a provider - without hard-coding an index or reading a stage
        # attribute. Empty for callers that don't need per-stage selection (text
        # runs behave identically whether or not this is supplied).
        self._stage_names = stage_names
        # The stage the executor is currently selecting a provider for. Set as the
        # loop enters each stage and before the initial selection, so _requirements
        # can be computed per stage rather than per run.
        self._current_stage_name: str | None = None
        self._stage_factory = stage_factory
        # Registry must be set before applying the strategy: free-eligibility
        # queries the registry for each provider's models.
        self._registry = registry if registry is not None else ModelRegistry()
        # Free-execution strategy (ADR-034). ANY (default) preserves prior
        # behaviour exactly. FREE_ONLY drops providers that expose no free
        # candidate for this run (e.g. Anthropic) so they are never invoked;
        # FREE_FIRST keeps them but orders free-eligible providers first.
        self._strategy = parse_strategy(settings.execution_strategy)
        ordered = self._apply_strategy_to_providers(list(providers))
        if not ordered:
            msg = f"No providers are eligible under the {self._strategy.value!r} execution strategy"
            raise ValueError(msg)
        self._providers = ordered
        self._report_line = reporter or (lambda _message: None)
        self._emit_event = events or (lambda _event: None)
        # Optional per-stage checkpoint sink (ADR-040). Called with
        # (stage_name, stage_index, output_model) after each stage completes.
        # Default no-op means CLI/tests behave exactly as before.
        self._checkpoint = checkpoint or (lambda _name, _index, _output: None)
        # Resume support (ADR-040): stages before start_index were completed in a
        # prior attempt and are skipped; the caller seeds `data` with the last
        # checkpoint's model so the remaining stages run against real upstream
        # output. 0 = normal full run.
        self._start_index = start_index
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
        # Models ruled out for the REMAINDER OF THE RUN, keyed by provider. Only
        # genuinely run-sticky model conditions land here (today none: every
        # disables_model FailureKind is stage-local per Phase G, so this stays a
        # structural hook rather than being populated - kept so a future run-sticky
        # model condition has a home without re-plumbing).
        self._excluded: dict[str, set[str]] = {info.name: set() for info in providers}
        # Models ruled out for the CURRENT STAGE only (transient/model-local
        # failures: server error, invalid/empty output, context limit, model-
        # unavailable, insufficient credit). Reset at each stage boundary so a
        # model that flaked in one stage is reconsidered in the next (Phase G).
        self._excluded_stage: dict[str, set[str]] = {info.name: set() for info in providers}
        # Providers skipped for the CURRENT STAGE only because their per-stage model
        # budget (max_models_per_provider_per_stage) was spent. Reset each stage, so
        # exhausting the budget in one stage does NOT retire the provider for the run
        # (Phase G - the scope-leak fix). Run-sticky provider disables live in
        # report.health.available instead.
        self._provider_stage_disabled: set[str] = set()
        # Distinct models tried on each provider for the CURRENT stage; reset at
        # each stage boundary. Enforces the per-provider-per-stage model cap.
        self._models_tried: dict[str, set[str]] = {info.name: set() for info in providers}

    # --- free-execution strategy ---------------------------------------------

    def _provider_has_free_candidate(self, provider: ProviderInfo) -> bool:
        """Whether a provider can supply at least one free-eligible candidate.

        Registry-backed providers (groq, openrouter, ollama) are free-eligible
        when any discovered/static model is free. Providers with no catalogue
        (anthropic, gemini) fall back to the configured model, whose free
        eligibility is decided by _configured_model_is_free.
        """
        models = self._registry.models_for(provider.name)
        if models:
            return any(m.free for m in models)
        return self._configured_model_is_free(provider.name)

    def _configured_model_is_free(self, provider: str) -> bool:
        """Free eligibility of a provider's single configured model.

        Delegates to the shared canonical rule (Phase 41C-4 extraction): Gemini
        flash tiers are free, NVIDIA is free (ADR-055), local providers are free,
        others default to not-free. Behaviour is unchanged from the former inline
        implementation.
        """
        return configured_model_is_free(self._settings, provider, self._all_provider_info())

    def _all_provider_info(self) -> list[ProviderInfo]:
        # The providers this executor was constructed with (post-strategy filter
        # this is a subset, but membership/local flags are unchanged).
        return list(getattr(self, "_providers", []))

    def _apply_strategy_to_providers(self, providers: list[ProviderInfo]) -> list[ProviderInfo]:
        """Filter/order providers for the active strategy (ADR-034).

        Called during __init__ before _providers is finalised, so it works on the
        passed-in list directly. ANY returns the list unchanged. FREE_ONLY keeps
        only providers with a free candidate. FREE_FIRST keeps all but orders
        free-eligible providers ahead of paid ones (stable within each group).
        """
        if self._strategy is ExecutionStrategy.ANY:
            return providers
        # Temporarily expose the list so the free-eligibility helpers can read
        # local flags during construction.
        self._providers = providers
        free = [p for p in providers if self._provider_has_free_candidate(p)]
        if self._strategy is ExecutionStrategy.FREE_ONLY:
            return free
        paid = [p for p in providers if p not in free]
        return free + paid

    # --- candidate selection -------------------------------------------------

    def _attempt_history(self) -> list[dict[str, object]]:
        """Sanitized attempt history for attaching to a terminal StageError.

        Only normalized fields (ADR-035); never keys, headers, or raw bodies.
        """
        return [
            {
                "stage": a.stage,
                "provider": a.provider,
                "model": a.model,
                "failure_kind": a.failure_kind,
                "status_code": a.status_code,
                "error_code": a.error_code,
            }
            for a in self.report.attempts
        ]

    def _ruled_out(self, provider: str) -> set[str]:
        """Models unavailable for THIS stage: run-sticky exclusions plus the
        stage-local ones. Read at every candidate-selection site so both scopes
        are honoured; the stage-local set is cleared at each stage boundary while
        the run-sticky set persists (Phase G)."""
        return self._excluded[provider] | self._excluded_stage[provider]

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
            if configured in self._ruled_out(provider.name):
                return []
            name = configured or f"{provider.name}-default"
            candidate = self._synthetic_candidate(provider.name, name)
            # Under FREE_ONLY a non-free synthetic candidate must not run.
            if self._requirements().free_only and not candidate.free:
                return []
            # Phase 38: an image-bearing run must not run on a non-image-capable
            # synthetic candidate (e.g. a text-only provider used as failover).
            if self._requirements().needs_images and not candidate.images_supported:
                return []
            return [candidate]
        scored = select_candidates(
            models,
            self._requirements(),
            limit=self._max_models_per_provider,
            configured=self._configured_model(provider.name) or None,
            excluded=self._ruled_out(provider.name),
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
            candidate = self._synthetic_candidate(provider.name, configured)
            if self._requirements().free_only and not candidate.free:
                return []
            if self._requirements().needs_images and not candidate.images_supported:
                return []
            return [candidate]
        scored = select_candidates(
            models,
            self._requirements(),
            limit=self._max_models_per_provider,
            configured=self._configured_model(provider.name) or None,
            excluded=self._ruled_out(provider.name) | exclude,
        )
        return [entry.model for entry in scored]

    def _requirements(self) -> StageRequirements:
        """Per-stage requirements (capability-driven).

        Text runs (no image stage) are unchanged: needs_images=False, so selection
        and fallback match pre-image behavior exactly. For an image run, ONLY the
        image-consuming stage requires an image-capable provider (needs_images=True);
        every other stage expresses only its real requirements (text/structured via
        the defaults) and does NOT exclude image-capable providers. A multimodal
        provider is therefore eligible wherever its capabilities satisfy the stage -
        image stage AND downstream text stages - selected by the existing chain
        order and failover, with no provider-specific rules.
        """
        needs_images = (
            self._image_stage_name is not None
            and self._current_stage_name == self._image_stage_name
        )
        return StageRequirements(
            free_only=self._strategy.requires_free,
            needs_images=needs_images,
        )

    def _synthetic_candidate(self, provider: str, name: str) -> ModelInfo:
        """A single-model candidate for a provider with no catalogue.

        Delegates to the shared candidates primitive (Phase 41C-4 extraction) so
        the executor and the clarification path build synthetic candidates by the
        same canonical rule.
        """
        return synthetic_candidate(self._settings, provider, name, self._all_provider_info())

    def _provider_supports_images(self, provider: str) -> bool:
        """Whether the registry marks this provider as image-capable (Phase 38)."""
        return provider_supports_images(provider, self._all_provider_info())

    def _configured_model(self, provider: str) -> str:
        return configured_model(self._settings, provider)

    def _settings_for(self, provider: ProviderInfo, model: ModelInfo) -> QAOpsSettings:
        return settings_for_model(self._settings, model)

    def _healthy_providers(self) -> list[ProviderInfo]:
        # A provider is usable this stage iff it is not run-disabled (auth /
        # PROVIDER_RATE_LIMIT, in report.health) AND has not spent its per-stage
        # model budget (self._provider_stage_disabled, cleared each stage). The
        # latter is stage-local so budget exhaustion never leaks into later stages
        # (Phase G).
        return [
            p
            for p in self._providers
            if self.report.health[p.name].available and p.name not in self._provider_stage_disabled
        ]

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

    def _provider_serves_current_stage(self, provider: ProviderInfo, model: ModelInfo) -> bool:
        """Whether the given provider yields any candidate for the current stage.

        Used at a stage boundary to decide if re-selection is needed. For text
        runs and same-requirement transitions the current provider still serves,
        so this returns True and nothing changes. When the image stage finishes
        and the next stage must exclude the image provider, the current (NVIDIA)
        provider yields no candidate, so this returns False and the caller
        re-selects from the normal text chain.
        """
        return bool(self._candidates(provider))

    def _stage_name_at(
        self, index: int, stages: list[PipelineStage[BaseModel, BaseModel]] | None = None
    ) -> str | None:
        """Name of the stage at `index`: from the supplied stage_names when
        available (lets us know the stage before building it), else from an
        already-built stage list. None when neither is available."""
        if 0 <= index < len(self._stage_names):
            return self._stage_names[index]
        if stages is not None and 0 <= index < len(stages):
            return str(stages[index].name)
        return None

    def run(self, data: BaseModel) -> BaseModel:
        """Run every stage, adapting model and provider as failures require."""
        self._current_stage_name = self._stage_name_at(self._start_index)
        provider = self._select_first_provider()
        model = self._candidates(provider)[0]
        stages = list(self._stage_factory(self._settings_for(provider, model)))
        current: BaseModel = data
        index = self._start_index

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
            # Reset stage-local failure scope (Phase G): models that failed with a
            # transient/model-local error in a prior stage, and providers that spent
            # their per-stage model budget, are reconsidered fresh this stage.
            # Run-sticky state (report.health disables from auth/PROVIDER_RATE_LIMIT,
            # and self._excluded) is deliberately NOT reset here.
            for staged in self._excluded_stage.values():
                staged.clear()
            self._provider_stage_disabled.clear()
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
                        attempts=self._attempt_history(),
                    ) from exc
                except Exception as exc:  # noqa: BLE001 - classified below
                    recovery = recovery_for_exception(exc)
                    self._health_for(provider.name, model.name).record_failure()
                    self.report.health[provider.name].record_failure(recovery.kind.value)
                    # Record a sanitized attempt for the failure history. Only
                    # normalized fields are kept - never keys, headers, or raw
                    # bodies (ADR-035).
                    exc_status = getattr(exc, "status_code", None)
                    exc_code = getattr(exc, "error_code", None)
                    self.report.attempts.append(
                        AttemptRecord(
                            stage=stage.name,
                            provider=provider.name,
                            model=model.name,
                            failure_kind=recovery.kind.value,
                            status_code=exc_status if isinstance(exc_status, int) else None,
                            error_code=exc_code if isinstance(exc_code, str) else None,
                            model_attempt_number=model_attempt_number,
                            provider_call_number=self._stage_provider_calls,
                        )
                    )
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
                                attempts=self._attempt_history(),
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
                # Persist this stage's output before advancing (ADR-040). The
                # sink is a no-op unless the orchestration layer supplied one, so
                # this does not change CLI/test behaviour or pipeline semantics.
                self._checkpoint(stage.name, index, output)
                current = output
                index += 1
                # Phase 40B: entering a new stage, refresh the current-stage name
                # and re-select the provider IF the stage just entered can no
                # longer run on the current provider (e.g. the image stage just
                # finished on NVIDIA and the next stage must exclude it). For text
                # runs and same-requirement transitions this is a no-op: the
                # current provider still passes, so nothing changes.
                if index < len(stages):
                    self._current_stage_name = self._stage_name_at(index, stages)
                    if not self._provider_serves_current_stage(provider, model):
                        provider = self._select_first_provider()
                        model = self._candidates(provider)[0]
                        stages = list(self._stage_factory(self._settings_for(provider, model)))
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
        if self._requirements().needs_images:
            msg = (
                "This run includes image evidence, but no configured provider "
                "supports image input. Set QAOPS_PROVIDER=nvidia (or another "
                "image-capable provider) and provide its API key. Visual evidence "
                "is never dropped and the run is never downgraded to text-only."
            )
            raise StageError("execution", msg)
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
            # Run-sticky: auth / PROVIDER_RATE_LIMIT (incl. Phase F daily-quota).
            # The provider is skipped for the rest of the run.
            self.report.health[provider.name].mark_unavailable(recovery.explanation)
        elif recovery.disables_model:
            # Stage-local (Phase G): a transient/model-local failure (server error,
            # invalid/empty output, context limit, model-unavailable, insufficient
            # credit) rules the model out for THIS stage only. It is reconsidered in
            # later stages, since none of these conditions is provider-wide or
            # necessarily permanent across the whole run.
            self._excluded_stage[provider.name].add(model.name)
            self._health_for(provider.name, model.name).mark_unavailable(recovery.explanation)

        retrying_same = recovery.action in {
            Action.RETRY_SAME,
            Action.RETRY_SAME_WITH_BACKOFF,
        }
        if retrying_same and attempts < self._max_attempts:
            return None

        if (
            self.report.health[provider.name].available
            and provider.name not in self._provider_stage_disabled
        ):
            # The per-provider cap counts distinct models tried for this stage
            # on this provider. Once that many have been attempted, the
            # provider's budget is spent even if more discovered models remain -
            # the choke point that stops a hundreds-long catalogue being walked.
            self._models_tried[provider.name].add(model.name)
            budget_left = len(self._models_tried[provider.name]) < self._max_models_per_provider
            if budget_left:
                already = self._ruled_out(provider.name) | self._models_tried[provider.name]
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
            # Budget spent for THIS stage only (Phase G): mark the provider
            # stage-disabled, NOT run-unavailable, so it is eligible again next
            # stage. Run-sticky disables (auth / PROVIDER_RATE_LIMIT) still use
            # report.health above.
            self._provider_stage_disabled.add(provider.name)
            self._report_line(
                f"  {provider.name} exhausted after "
                f"{len(self._models_tried[provider.name])} model(s) this stage"
            )

        replacement_provider = self._next_provider(provider)
        if replacement_provider is None:
            raise StageError(
                stage_name,
                f"All providers failed. Last error from {provider.name}/{model.name}: {exc}",
                attempts=self._attempt_history(),
            ) from exc
        replacement_model = self._candidates(replacement_provider)[0]
        self.report.provider_switches.append((stage_name, provider.name, replacement_provider.name))
        self._report_line(
            f"  switching {provider.name} -> {replacement_provider.name}/{replacement_model.name}"
        )
        return replacement_provider, replacement_model
