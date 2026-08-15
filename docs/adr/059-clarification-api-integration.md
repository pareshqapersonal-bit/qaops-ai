# ADR-059: Clarification API / run-lifecycle integration (Phase 41C-1)

**Status:** Accepted · **Date:** 2026-08-14 · **Phase 41C-1** · **Relates to:** Phase 41 review, ADR-058 (41B agent)

## Context

Phases 41A/41B built the clarification state layer and the ClarificationAgent as
isolated, pipeline-free modules. Phase 41C wires them into the run lifecycle and API so
a user can submit a PRD/ticket, receive clarification questions, answer them, and reach
a readiness gate - without disturbing the existing one-shot flow. 41C-1 covers the
service + bounded analysis + two endpoints + opt-in flag; the handoff to the
test-design pipeline is 41C-2.

## Decision

- **Bounded analysis via composition (option 8(a))**: a new `ClarificationService`
  runs the EXISTING `ChunkedRequirementAnalyzer` + `GapAnalyzer` stages, constructed
  directly (as 41B's tests already do), evidence bound to the analyzer only (40B). No
  DesignService/executor/pipeline-stage change. One LLM client is shared across
  analyzer + gap + agent.
- **Opt-in `clarify=true`** on the existing submit flow. When false/omitted the run
  schedules the unchanged `execute_run` one-shot task; when true it schedules a new
  `execute_clarification_analysis` task that runs bounded analysis, generates the first
  question batch, persists 41A state, and parks the run in AWAITING_CLARIFICATION (or
  READY_FOR_TEST_DESIGN when there were no blocking gaps).
- **Two additive RunStatus values**: AWAITING_CLARIFICATION, READY_FOR_TEST_DESIGN
  (same additive pattern as Phase 25; one-shot runs never enter them).
- **Two endpoints**: `GET /api/v1/runs/{id}/clarifications` (questions + readiness; 404
  unknown run, 409 no clarification in progress) and
  `POST /api/v1/runs/{id}/clarifications/answers` (apply structured answers; 400 on
  malformed/contradictory, 409 not-awaiting/round-cap). Both reuse the existing
  response conventions.
- **Round cap = 5** (decision 8(6)): after 5 rounds with blockers still open, answers
  must be resubmitted with proceed_with_assumptions=true, which marks remaining
  questions skipped and records assumptions.
- **Readiness** is computed only by the existing Phase 41A `compute_readiness()`.

## Consequences

- A clarification-enabled run pauses server-side with its state in
  `workspace/clarification/state.json`, so it survives browser close and even a server
  restart (stronger than the in-process pipeline resume).
- The one-shot flow is byte-identical when clarify is false/omitted (verified: the
  default schedules execute_run, not the clarification task).
- No handoff to the test-design pipeline yet (41C-2), so a READY run does not auto-run
  design; the start-test-design endpoint is intentionally absent here.

## Scope

Changed: `qaops/clarification/service.py` (new), `qaops/clarification/agent.py`
(answer-processing constructor), `qaops/api/runner.py` (clarification task),
`qaops/api/schemas.py` (schemas), `qaops/api/app.py` (flag + 2 endpoints + mapper),
`qaops/api/runs.py` (2 RunStatus values), tests. Untouched: executor, selector,
DesignService, GapAnalyzer (40A), per-stage selection (40B), structured.py, NVIDIA
client, image ingestion/sidecar, RequirementAnalyzer, pipeline stages, 41A models,
frontend, and the one-shot execute_run path.
