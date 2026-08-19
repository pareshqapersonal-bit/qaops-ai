"""Resilient structured LLM call (Phase 41C-4).

A small, shared resilience primitive that gives a single LLM call the same
provider failover the AdaptiveExecutor applies per stage - WITHOUT importing the
executor's stage/checkpoint orchestration. It reuses the two pure decision
functions the executor itself relies on:

- ``select_candidates`` (selector) ranks provider/model candidates for the call's
  requirements (text vs image, free_only, configured-first);
- ``recovery_for_exception`` (policy) classifies a failure into the same actions
  the executor uses (RETRY_SAME[_WITH_BACKOFF] vs NEXT_MODEL vs terminal).

The clarification path composes analyzer -> gap -> agent directly (ADR-059,
option 8(a)) and so does not inherit executor resilience; this helper restores it
for those calls. It does not modify executor.py, selector.py, policy.py, the
provider clients, or the registry - it only imports their public primitives.

Design invariants:
- A FRESH client is built per attempt (preserves the Phase 41C-3 fix: a client's
  httpx pool must not be reused across run_with_deadline's per-call event loops).
- A NVIDIA 500 ("EngineCore") classifies as NEXT_MODEL, so it fails over to the
  next eligible provider rather than hammering NVIDIA (per the policy, not a new
  rule invented here).
- RETRY_SAME / RETRY_SAME_WITH_BACKOFF follow the existing Recovery exactly,
  including its backoff_seconds; no new retry rule is introduced.
- Bounded: every candidate is tried at most ``max_attempts_per_model`` times, and
  the candidate list itself is finite, so exhaustion raises a clear error - never
  an infinite loop.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from qaops.execution.candidates import build_candidate_models, settings_for_model
from qaops.execution.models import ModelInfo, ModelRegistry
from qaops.execution.policy import Action, recovery_for_exception
from qaops.execution.selector import StageRequirements, select_candidates
from qaops.llm.factory import create_client
from qaops.services.design_service import fallback_providers

if TYPE_CHECKING:
    from collections.abc import Callable

    from qaops.config import QAOpsSettings
    from qaops.llm import LLMClient


class ResilientCallError(RuntimeError):
    """All eligible provider/model candidates failed for a resilient call."""


def _candidate_models(settings: QAOpsSettings, registry: ModelRegistry) -> list[ModelInfo]:
    """Ordered candidate models across the configured provider chain.

    Delegates entirely to the shared build_candidate_models primitive (the same
    one the executor uses), so discovery, synthetic candidates, and free/image/
    text/structured capability flags follow one canonical rule - no duplicated or
    approximated provider-selection logic lives here. fallback_providers() supplies
    the configured-first provider order and is correctly key-gated, so failover
    only reaches providers the deployment has credentials for.
    """
    return build_candidate_models(
        providers=fallback_providers(settings), settings=settings, registry=registry
    )


def resilient_structured_call[T](
    *,
    settings: QAOpsSettings,
    requirements: StageRequirements,
    run_call: Callable[[LLMClient], T],
    registry: ModelRegistry | None = None,
    candidates: list[ModelInfo] | None = None,
    max_attempts_per_model: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run ``run_call`` against ranked candidates with policy-driven failover.

    ``run_call(client)`` performs the actual structured request (e.g. a stage's
    ``.run()``); the helper supplies a fresh client per attempt and, on failure,
    consults the existing recovery policy to decide retry-same vs next-model.

    ``candidates`` overrides the assembled candidate list (used by tests to inject
    a deterministic provider set); in production it is derived from the configured
    provider chain, which is correctly key-gated - failover only reaches providers
    the deployment actually has credentials for.

    Raises ResilientCallError if every eligible candidate is exhausted, or
    re-raises immediately on a terminal (non-retryable, non-failover) failure.
    """
    registry = registry if registry is not None else ModelRegistry()
    models = candidates if candidates is not None else _candidate_models(settings, registry)
    excluded: set[str] = set()
    excluded_providers: set[str] = set()
    last_exc: Exception | None = None

    # Bound the outer loop by the number of distinct candidates; each is tried at
    # most max_attempts_per_model times. Both are finite -> no infinite loop.
    for _ in range(len(models) + 1):
        # Drop every model of a disabled provider before ranking, reproducing the
        # executor's provider-level disabling (report.health[provider].available):
        # a DISABLE_AND_SWITCH failure skips ALL that provider's models, not just
        # the one that failed. Model-level exclusions are passed to the selector.
        eligible = [m for m in models if m.provider not in excluded_providers]
        scored = select_candidates(
            eligible,
            requirements,
            limit=1,
            configured=settings.provider,
            excluded=excluded,
        )
        if not scored:
            break  # no eligible candidate remains
        model = scored[0].model

        attempt = 0
        while attempt < max_attempts_per_model:
            attempt += 1
            call_settings = settings_for_model(settings, model)
            # Fresh client per attempt (Phase 41C-3): never reuse a client whose
            # transport is bound to a closed event loop.
            client = create_client(call_settings)
            try:
                return run_call(client)
            except Exception as exc:  # noqa: BLE001 - classified below, then re-raised/failed-over
                last_exc = exc
                recovery = recovery_for_exception(exc)
                if recovery.action in {
                    Action.RETRY_SAME,
                    Action.RETRY_SAME_WITH_BACKOFF,
                }:
                    if recovery.backoff_seconds:
                        sleep(recovery.backoff_seconds * attempt)
                    continue  # retry the SAME candidate
                # Honour the policy's own disabling semantics exactly (the same
                # Recovery properties the executor uses), rather than collapsing
                # everything to model-level exclusion:
                #   disables_provider (DISABLE_AND_SWITCH: auth / provider-wide
                #     rate limit) -> skip the WHOLE provider for the rest of the
                #     call, so sibling models are not tried.
                #   disables_model (NEXT_MODEL / DROP_MODEL_AND_CONTINUE /
                #     LARGER_CONTEXT_MODEL: incl. the NVIDIA EngineCore 500 ->
                #     UNKNOWN -> NEXT_MODEL path) -> drop only this model; other
                #     models on the provider remain eligible.
                if recovery.disables_provider:
                    excluded_providers.add(model.provider)
                    break  # advance; provider-filter drops its siblings next pass
                if recovery.disables_model:
                    excluded.add(model.name)
                    break  # advance to the next eligible candidate
                # Truly terminal (e.g. ABORT): surface as-is.
                raise
        excluded.add(model.name)

    msg = "All eligible provider/model candidates failed for the clarification call."
    raise ResilientCallError(msg) from last_exc
