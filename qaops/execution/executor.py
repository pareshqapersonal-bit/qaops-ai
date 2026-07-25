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
from qaops.execution.models import (
    ModelHealth,
    ModelInfo,
    ModelRegistry,
    filter_by_capability,
)
from qaops.execution.policy import Action, Recovery, recovery_for
from qaops.execution.registry import ProviderHealth, ProviderInfo

logger = logging.getLogger(__name__)

# Builds the pipeline stages for one provider/model pairing. Called again on
# every switch, so stage construction stays outside this module entirely.
StageFactory = Callable[[QAOpsSettings], Sequence[PipelineStage[BaseModel, BaseModel]]]

Reporter = Callable[[str], None]

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
        self._max_attempts = max_attempts_per_model
        self._sleep = sleep
        self.report = ExecutionReport(
            health={info.name: ProviderHealth(name=info.name) for info in providers}
        )
        # Models already ruled out this run, keyed by provider.
        self._excluded: dict[str, set[str]] = {info.name: set() for info in providers}

    # --- candidate selection -------------------------------------------------

    def _candidates(self, provider: ProviderInfo) -> list[ModelInfo]:
        """Compatible models for a provider, best first, minus those ruled out."""
        models = self._registry.models_for(provider.name)
        if not models:
            configured = self._configured_model(provider.name)
            if not configured:
                return []
            return [ModelInfo(name=configured, provider=provider.name)]
        return filter_by_capability(
            models,
            structured_output=True,
            exclude=self._excluded[provider.name],
        )

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
            while True:
                attempts += 1
                try:
                    output = stage.run(current)
                except Exception as exc:  # noqa: BLE001 - classified below
                    recovery = recovery_for(str(exc))
                    self._health_for(provider.name, model.name).record_failure()
                    self.report.health[provider.name].record_failure(recovery.kind.value)
                    self._report_line(
                        f"  {stage.name}: {provider.name}/{model.name} "
                        f"failed ({recovery.kind.value})"
                    )
                    logger.warning(
                        "execution.stage_failed stage=%s provider=%s model=%s kind=%s",
                        stage.name,
                        provider.name,
                        model.name,
                        recovery.kind.value,
                    )

                    target = self._plan_recovery(
                        recovery, provider, model, attempts, stage.name, exc, tried_here
                    )
                    if target is None:
                        if recovery.backoff_seconds:
                            self._sleep(recovery.backoff_seconds * attempts)
                        continue

                    tried_here.add(model.name)
                    provider, model = target
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
                current = output
                index += 1
                break

        return current

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
            # Exclude the current model and any already tried for this stage, so
            # a retryable failure that keeps recurring cannot cycle models here.
            exhausted = tried_here | {model.name}
            siblings = [
                candidate
                for candidate in self._candidates(provider)
                if candidate.name not in exhausted
            ]
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
            self.report.health[provider.name].mark_unavailable("all compatible models failed")
            self._report_line(f"  {provider.name} exhausted; every model failed")

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
