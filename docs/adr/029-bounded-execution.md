# ADR-029: Bounded, ranked candidate selection and structured execution progress

**Status:** Accepted · **Date:** 2026-07-25 · **Relates to:** ADR-026, ADR-027, ADR-028

## Context

Live testing of a real PDF run stayed `running` for over 30 minutes. It was not
deadlocked — the executor was actively trying a very large number of discovered
OpenRouter models, each returning `insufficient_credit`, then hitting
schema-validation failures on others.

The root cause: `AdaptiveExecutor` drew its candidates straight from
`ModelRegistry.models_for`, which returns every discovered model — hundreds for
OpenRouter. `filter_by_capability` filtered on structured-output support, but
discovered models default to `structured_output=True`, so nothing was filtered.
With a credit-exhausted account, all hundreds passed the filter and each was
tried in turn. The Phase 15 loop guard prevented an *infinite* loop, but
"bounded" at hundreds of network round-trips is not useful.

Live discovery must supply candidates; it must not imply that every discovered
model should be attempted.

## Decision

Insert an explicit selection layer between discovery and execution, add two
independent recovery bounds, and emit structured progress events.

1. **A candidate selector (`selector.py`) filters, ranks, and bounds.** Given a
   provider's discovered models it drops incompatible ones, scores the rest
   deterministically, and returns at most N. The executor never iterates the
   full catalogue — `_candidates` calls the selector, which is the single choke
   point.

2. **Ranking is deterministic and capability-driven.** Score signals, highest
   first: the configured/preferred model (always leads), known structured-output
   support, curated priority, context and output headroom for the stage, and a
   mild nudge toward free models. Ties break by model name, so equal candidates
   have a stable order regardless of discovery order. No LLM is involved;
   selection is pure and testable.

3. **Two independent bounds, from settings.**
   - `max_models_per_provider_per_stage` (default **5**) caps the *distinct
     models* tried on one provider for one stage. Once spent, execution moves to
     the next provider. It counts model candidates, never same-model schema
     retries.
   - `max_stage_recovery_attempts` (default **12**) caps the *total* recovery
     actions (model switches plus provider switches) for one stage, so a chain
     of providers cannot compound into a long run. Same-model transient retries
     do **not** consume this budget — they are bounded separately by
     `max_attempts_per_model`. When the budget is spent, the stage raises the
     existing `StageError` with a concise diagnostic; completed-stage
     checkpoints are preserved.

   Defaults chosen: 5 models covers a primary plus a few fallbacks without
   inviting a long crawl; 12 recovery actions comfortably spans two or three
   providers at the 5-model cap while still terminating quickly. Both are
   configurable and validated.

4. **`insufficient_credit` stays bound-only.** The live 402 carried a "can only
   afford N tokens" figure, which tempts an A/B split (model-affordability vs
   account exhaustion). We do not split. That number reflects the current
   request's token math, not a reliable account-level signal, and the live run
   showed every model failing regardless — so inferring "account exhausted"
   from it would be guessing, which the phase brief explicitly cautions against.
   The per-provider model cap already bounds repeated credit failures to N
   attempts, which is the real protection. Credit failures therefore try the
   next ranked model until the cap, then switch providers.

5. **Run-local failure signals refine recovery**, without any persistent
   reputation store: a model that returns `model_unavailable` is dropped from
   candidates; `context_limit` prefers larger-context candidates;
   `invalid_output` after its retries abandons that model for the stage; `auth`
   disables the provider (unchanged Phase 15 policy). All state is per-run.

6. **Structured execution events (`events.py`) replace log-string parsing.** The
   executor emits an `ExecutionEvent` (type, stage, stage index/count, provider,
   model, attempt counts, safe message) at each boundary and failure. The CLI
   renders events as the same text as before; the API converts them into run
   progress. The executor depends only on an event-sink callback and knows
   nothing about HTTP — the dependency arrow is executor → events → {CLI, API}.

7. **API run progress and richer terminal state.** `GET /api/v1/runs/{id}` now
   carries a `progress` object (current stage, position, provider, model, models
   attempted, recovery attempts, safe message) while running and preserved after
   completion. Failed runs additionally expose `failed_stage` and
   `recovery_attempts`. No secrets and no raw provider payloads appear — event
   messages are composed from known-safe fields.

## Consequences

- The live scenario is fixed: a 300-model credit-exhausted catalogue now yields
  at most 5 attempts on that provider before failover, and a bounded total
  before a clean failure. Verified by a stress test.
- The bound is a deliberate ceiling on *fidelity of search*: a genuinely
  available model ranked sixth on a provider will not be tried if the first five
  fail. That is the correct trade — the alternative is the unbounded crawl this
  ADR exists to remove — and the limit is configurable for users who want a
  wider search.
- Progress is observable over HTTP without the API parsing terminal output,
  which the phase forbids.
- Still no agentic behaviour: selection is a deterministic score-and-sort, the
  bounds are fixed integers, and events are structured records. Nothing plans or
  chooses tools.
