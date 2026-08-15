# ADR-060: Clarification -> test-design handoff (Phase 41C-2)

**Status:** Accepted · **Date:** 2026-08-14 · **Phase 41C-2** · **Relates to:** ADR-059 (41C-1), Phase 41 review

## Context

Phase 41C-1 let a clarification-enabled run reach READY_FOR_TEST_DESIGN but nothing
consumed that state. 41C-2 adds the handoff: a READY run produces clarified
requirements and runs the existing test-design pipeline, reusing the analyzer + gap
work already done during clarification rather than re-running it.

## Decision

- **`start-test-design` endpoint** (`POST /api/v1/runs/{id}/start-test-design`):
  validates the run is at READY_FOR_TEST_DESIGN with `readiness.ready` (409 otherwise),
  guards against duplicate/late starts (only a run parked at READY may start), then
  schedules the handoff and returns 202.
- **Clarified requirements artifact**: the ClarificationService persists the analyzed
  requirements at clarification start (`analyzed_requirements.json`) and, at handoff,
  applies the recorded answers by AUGMENTING those requirements (41B agent - originals
  and order preserved) into `clarified_requirements.json`.
- **Reuse the existing execution path via the `requirements` entry point**: the
  clarified JSON auto-detects as the `requirements` entry point, so placing it as the
  run's input and delegating to the EXISTING `execute_run` starts the pipeline at
  business_rule_extractor - the analyzer and gap stages are NOT re-run. No
  DesignService/executor/pipeline-stage change; the handoff only stages input and
  reuses one-shot execution.
- **Answers reach downstream stages**: applied answers live in each requirement's
  `assumptions` (an existing field parse_requirements round-trips), so business rules,
  scenarios, and test cases are generated from the clarified requirements.

## Consequences

- End-to-end: submit (clarify=true) -> questions -> answers -> READY -> start-test-design
  -> pipeline (requirements entry) -> COMPLETED. Verified by test, including that the
  design phase runs with no analyzer response scripted (proof analyzer isn't re-run).
- Requirement IDs stay stable across the handoff (order preserved; parse_requirements
  reassigns IDs deterministically in order).
- Safe under invalid/not-ready/duplicate-start: each is a 409; handoff preparation is
  idempotent (re-running overwrites the same artifact).
- One-shot flow unchanged (clarify omitted -> execute_run, no handoff path touched).

## Scope

Changed: `qaops/clarification/service.py` (persist analyzed requirements +
prepare_test_design + ClarificationNotReadyError + serialization helpers),
`qaops/api/runner.py` (start_test_design_from_clarification task),
`qaops/api/app.py` (start-test-design endpoint), tests. Untouched: DesignService,
executor, selector, GapAnalyzer (40A/40B), pipeline stages, structured.py, providers,
NVIDIA, image ingestion, 41A models, 41B agent behavior, frontend, and the one-shot
execute_run path (reused as-is).
