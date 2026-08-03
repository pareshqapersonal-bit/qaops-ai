# ADR-041: The Orchestrator Agent — QAOps' first agentic capability

**Status:** Accepted · **Date:** 2026-08-02 · **Relates to:** ADR-040 (checkpointing/resume), ADR-011/014/015 (LLM generates, code validates)

## Context

Through Phase 25, QAOps is a deterministic pipeline: entry-point detection fixes
a stage list, the `AdaptiveExecutor` runs it with bounded per-stage retry and
provider failover, `CheckpointStore` persists each stage, and `resume()` restarts
from the last checkpoint. All of this is mechanical — there is no component that
*reasons* about how a run should execute or *explains* why.

Phase 26 introduces the first agent. The requirement is sharp: add reasoning
without touching determinism. The agent must decide and explain HOW the pipeline
executes; it must never generate or modify WHAT the pipeline produces
(requirements, business rules, gaps, scenarios, conditions, test cases,
coverage). Those seven artifact types remain owned exclusively by the
deterministic stages.

## Decision

Add an additive `qaops/agent/` package containing an `OrchestratorAgent` that
wraps — never replaces — the existing execution architecture.

### Separation of concerns: reasoning vs. artifacts

- **The pipeline produces artifacts.** Every requirement, rule, gap, scenario,
  condition, test case, and coverage number continues to come from the existing
  deterministic stages, unchanged.
- **The agent reasons about execution.** It builds an execution plan, decides
  resume-vs-restart, records the decisions, delegates execution to the unchanged
  `DesignService`, and produces a post-run reflection.

The agent has no method that emits a pipeline artifact; its only execution path,
`execute()`, delegates to `DesignService.run()` / `.resume()`.

### Two-layer design: deterministic structure, optional LLM prose

Both planning and reflection separate a deterministic structural layer from an
optional LLM reasoning layer:

- **Structure (deterministic):** which stages run and which are reused comes from
  the entry point (`stage_names_for`) and `CheckpointStore`; the resume decision
  and the recorded `Decision`s are computed from checkpoint state; the
  reflection's successes/failures/retries/recovered/skipped are computed from the
  manifest and attempt history; the clarification recommendation is driven by the
  deterministic ambiguity signal (unresolved conditions and gap count already
  produced by Phases 22–23). This layer alone is a complete, correct result.
- **Reasoning (optional LLM):** the LLM may enrich the human-readable per-step
  `reason` prose and the reflection narrative. It is explicitly forbidden, in the
  system prompt, from adding/removing/reordering stages or producing any pipeline
  content, and its output is applied only to matching stages' prose. If the LLM
  is absent or returns unusable output, the deterministic text is used and the
  run is unaffected. The LLM never changes which stages run.

### Determinism guarantee

Execution is performed by `DesignService.run()` / `.resume()` exactly as in
Phase 25. The agent chooses which of the two to call (based on whether
checkpoints exist) and adds plan/reflection *around* them. When the agent makes
no structural intervention — no checkpoints, so a full run — behaviour is
byte-identical to Phase 25. This is asserted by a test comparing agent-driven
artifacts against a plain pipeline run on identical input.

### Decision-making rules (all deterministic)

- **Checkpoints exist → resume, not restart** (reuse completed stages). The
  alternative (restart) is recorded as considered-and-rejected.
- **A stage already succeeded → reuse it** — surfaced as a `REUSE` plan step.
- **A stage repeatedly fails → stop, don't loop.** The executor's bounded retry
  is the real stop; the agent's contribution is to *not* recommend resuming again
  at a stage that already exhausted recovery, and to say so in the reflection.
- **Ambiguity over threshold → recommend clarification**, driven by the
  deterministic unresolved-conditions fraction and gap count — the agent never
  forms its own ambiguity opinion.

### Extensible package, one agent today

The package is laid out for future agents without another refactor:
`base.py` (the `Agent` ABC), `models.py` (plan/decision/reflection Pydantic
models), `planner.py`, `reflection.py`, `orchestrator.py`. This is not multiple
agents today — it is the extension point for them.

### Additive API and UI

Backward compatibility means additive, not frozen. New response fields (`plan`,
`reflection`) on the run status, new schemas, and new UI panels (execution plan +
decisions, execution reflection) are added. No existing endpoint, field, or
workflow is removed or changed; runs created before the agent simply have null
plan/reflection and render exactly as before. Plan/reflection generation in the
runner is best-effort — a failure there never turns a successful run into a
failed one, nor blocks execution.

## What makes this the first agentic capability

Prior phases were mechanical: fixed control flow, no reasoning. The
OrchestratorAgent is the first component that (a) forms a goal-directed plan,
(b) chooses between execution strategies (resume vs restart) with recorded
justification and considered alternatives, and (c) reflects on the outcome and
recommends next actions. It exhibits plan → act → reflect, the minimal shape of
an agent — while every artifact remains deterministically produced.

## Why determinism is preserved

Because the agent never executes a stage itself. It delegates to the same
`DesignService` entry points the CLI and API already use, so the pipeline's
inputs, ordering, validation, retry, and checkpointing are untouched. The LLM
touches only advisory prose, never control flow or artifacts, and degrades to
deterministic text on failure. No-intervention runs are provably identical to
Phase 25.

## Consequences

- QAOps gains explainable orchestration: users see why stages run, why a run
  resumed, and what to do about ambiguity — without any change to the artifacts.
- The determinism and backward-compatibility guarantees are preserved and tested.
- A clear seam exists for future agents (e.g. a triage or prioritization agent)
  to be added against the same base without reworking the pipeline.

## Alternatives considered

- **Agent generates/curates artifacts:** rejected outright — violates the core
  principle and the determinism guarantee.
- **Agent as a pure library, no API/UI:** rejected per the clarified requirement
  that the agent be a first-class, surfaced capability (additive, not intrusive).
- **LLM decides the stage list:** rejected — non-deterministic control flow. The
  stage list stays derived from the entry point; the LLM only explains it.

## Limitations / future work

The agent's reasoning is advisory and its LLM enrichment best-effort. Retry
"worthwhileness" is currently a conservative deterministic signal (don't re-resume
a stage that exhausted recovery), not a learned judgement. Future agents and
richer goal interpretation can build on this base; a dedicated `POST /plan`
preview endpoint and multi-agent routing are candidate extensions.
