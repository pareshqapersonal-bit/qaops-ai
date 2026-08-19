# ADR-063: Shared candidate builder + clarification resilient-call (Phase 41C-4)

**Status:** Accepted · **Date:** 2026-08-16 · **Phase 41C-4** · **Relates to:** ADR-059 (41C-1 clarification), ADR-062 (41C-3 client lifecycle), ADR-029 (bounded candidates), ADR-027 (provider vs model failover), ADR-055 (NVIDIA free)

## Context

The clarification path (ClarificationService: requirement_analyzer -> gap_analyzer ->
ClarificationAgent) composes stages directly and bypasses the AdaptiveExecutor
(ADR-059, option 8(a)). It therefore inherited none of the executor's provider
failover: an intermittent NVIDIA "EngineCore" HTTP 500 - which classifies as
NEXT_MODEL and which the executor absorbs by moving to the next provider - aborted the
whole clarification run. The normal one-shot PRD flow, running through the executor,
survives the same 500.

A first implementation added a resilient-call helper but re-created candidate-assembly
logic (synthetic candidates, free-eligibility, image capability) inside it. That
duplicated executor-owned logic and had already diverged: a blanket
`_FREE_ELIGIBLE_PROVIDERS` set treated Gemini as wholesale free, whereas the executor's
canonical rule frees only Gemini *flash* tiers. Under FREE_ONLY this would admit a paid
Gemini model the executor rejects.

## Decision

Extract the candidate-assembly logic into a shared, pure primitive and have both the
executor and the clarification helper consume it - so provider-selection rules have a
single source of truth and cannot diverge.

- **New `qaops/execution/candidates.py`**: `build_candidate_models(providers, settings,
  registry)` plus the canonical capability helpers (`configured_model_is_free`,
  `provider_supports_images`, `synthetic_candidate`, `models_for_provider`,
  `settings_for_model`) and the `MODEL_FIELD` map. This is a behaviour-preserving move
  of the executor's former private methods; no rule is approximated (Gemini free only
  for flash, NVIDIA free per ADR-055, local free, else not-free).
- **`executor.py`** now delegates its `_synthetic_candidate`,
  `_provider_supports_images`, `_configured_model`, `_configured_model_is_free`, and
  `_settings_for` to the shared primitive; its `_MODEL_FIELD` constant is removed in
  favour of the shared one. Behaviour is unchanged, proven by the full executor test
  suite (245 tests) staying green.
- **`resilient_call.py`** (`resilient_structured_call`): builds candidates via
  `build_candidate_models`, ranks via the unchanged `select_candidates`, and classifies
  failures via the unchanged `recovery_for_exception`. It honours the policy's own
  `Recovery` disabling semantics exactly, mirroring the executor:
  RETRY_SAME[_WITH_BACKOFF] retries the same candidate honouring the policy's backoff;
  `recovery.disables_provider` (DISABLE_AND_SWITCH - authentication / provider-wide rate
  limit) adds the provider to a local `excluded_providers` set so the WHOLE provider is
  skipped for the rest of the call (its sibling models are not tried), reproducing the
  executor's `report.health[provider]` disabling; `recovery.disables_model` (NEXT_MODEL /
  DROP_MODEL_AND_CONTINUE / LARGER_CONTEXT_MODEL - including the NVIDIA EngineCore 500 ->
  UNKNOWN -> NEXT_MODEL path) excludes only the failing model, leaving sibling models on
  the same provider eligible; anything else surfaces as-is. Candidate selection filters
  out every model of an excluded provider before ranking. Exhaustion raises a bounded
  `ResilientCallError`. A FRESH client is built per attempt, preserving the Phase 41C-3
  event-loop fix.

  Correction note: an earlier 41C-4 draft collapsed DISABLE_AND_SWITCH /
  DROP_MODEL_AND_CONTINUE / SWITCH_PROVIDER into a single model-level exclusion. That
  diverged from the executor for DISABLE_AND_SWITCH: after an auth or provider-wide
  rate-limit failure the helper would still try sibling models of the same (effectively
  disabled) provider. The `excluded_providers` set above restores exact provider-vs-model
  disabling parity. This does not change the NVIDIA-500 case (NEXT_MODEL, model-level on
  both paths) that 41C-4 targets, and reuses the policy's existing `Recovery` properties
  rather than adding a new rule - so policy.py and selector.py are untouched.
- **`ClarificationService`** runs analyzer / gap / agent through the helper with the
  right StageRequirements: the analyzer requires image-capable providers only when the
  run carries image evidence; gap analysis excludes image providers downstream (Phase
  40B); the agent is text-only.

`select_candidates`, `policy`, `deadline`, the provider clients, the registry, and
DesignService are unchanged. `fallback_providers` stays in `design_service.py` and is
imported (moving it would touch protected DesignService for no functional gain).

## Consequences

- Clarify-ON gets the same policy-driven provider failover as the normal flow: a NVIDIA
  500 fails over to the next eligible provider instead of aborting.
- One canonical candidate-assembly rule, shared by executor and clarification - the
  Gemini-flash divergence is structurally impossible.
- Bounded and terminating: each candidate is tried at most `max_attempts_per_model`
  times and the candidate list is finite.
- Failover only reaches providers the deployment has keys for (fallback_providers is
  key-gated), matching executor behaviour.

## Scope / protected

Changed: `candidates.py` (new), `executor.py` (delegation only), `resilient_call.py`,
`clarification/service.py`, and three migrated 41C test files + one new 41C-4 test file.
Untouched: selector.py, policy.py, deadline.py, DesignService behaviour, pipeline
stages, RequirementAnalyzer, GapAnalyzer, provider clients, provider registry,
structured.py, image ingestion, API, frontend, 41A state/readiness, 41B agent
behaviour. No new ClarificationStatus values (deferred). Version stays 0.18.0-dev.
