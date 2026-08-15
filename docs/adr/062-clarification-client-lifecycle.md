# ADR-062: Clarification client lifecycle fix (Phase 41C-3)

**Status:** Accepted · **Date:** 2026-08-15 · **Phase 41C-3** · **Relates to:** ADR-059 (41C-1), ADR-031 (hard deadline)

## Context

Clarification-enabled runs (clarify=true) failed on Groq with `[groq] Connection error`,
while the normal one-shot PRD flow (clarify off) worked. Root cause: `ClarificationService`
built ONE client in `start()` and reused it across three sequential LLM calls
(requirement_analyzer -> gap_analyzer -> ClarificationAgent).

A provider client such as `GroqClient` creates its `AsyncOpenAI` (and its httpx
connection pool) once and reuses it. `run_with_deadline` (ADR-031) runs every provider
call under its own short-lived `asyncio.run()` event loop, which is closed when the call
returns. The connection pool binds to the FIRST loop; the second call opens a new loop
and reuses a pool bound to the now-closed loop, so httpx raises
`APIConnectionError: "Connection error."` on the second call. The normal path was immune
because the executor builds a fresh client per stage-selection (`build_stages ->
create_client`), so its calls do not reuse one client across closed loops.

## Decision

Build a FRESH client per LLM call inside the clarification path - exactly mirroring the
executor's per-stage `create_client`. `ClarificationService._analyze` now constructs its
own client for the analyzer and another for the gap analyzer; `start()` constructs a
separate client for the agent's question-generation call. No client is reused across the
separate event loops `run_with_deadline` creates.

This is the smallest change that guarantees the invariant. It touches only
`ClarificationService`; the deadline mechanism, the Groq transport, and every provider
client are unchanged.

## Consequences

- Clarify-ON performs analyzer -> gap_analyzer -> agent on Groq without the
  connection-error lifecycle problem (each call has a fresh transport).
- Provider selection, model, `max_output_tokens` (4000 in prod), timeout, retry, and
  execution strategy are all unchanged - they still flow through `settings` into
  `create_client`, which is a cheap constructor with no shared global state.
- 41C API contracts and 41A/41B/41C behavior are preserved; `submit_answers` stays
  client-free (its methods are pure).
- Slightly more client construction (three short-lived clients instead of one) - a
  negligible cost that matches what the normal pipeline already does per stage.

## Scope

Changed: `qaops/clarification/service.py` (`_analyze` no longer takes a shared client and
builds its own per stage; `start()` builds a fresh client for the agent). Untouched:
executor, selector, DesignService, pipeline stages, GapAnalyzer, RequirementAnalyzer,
`deadline.py`, GroqClient transport, provider registry, NVIDIA client, image ingestion,
41A models/readiness, 41B agent behavior, and the one-shot flow.
