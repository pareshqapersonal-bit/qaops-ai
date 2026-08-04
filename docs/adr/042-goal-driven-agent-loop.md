# ADR-042: The goal-driven agent loop (observe → decide → act → reflect)

**Status:** Accepted · **Date:** 2026-08-03 · **Relates to:** ADR-041 (Orchestrator Agent), ADR-040 (checkpointing/resume), ADR-035 (failure observability)

## Context

Phase 26 (ADR-041) introduced the Orchestrator Agent as a single-shot
plan → act → reflect wrapper: it planned, called `DesignService.run()` or
`.resume()` exactly once, and reflected. If that single act failed, the agent
propagated the error and stopped — it could not decide to resume and try again.

Phase 27 evolves this into a goal-driven loop that manages execution until a
terminal condition, while keeping the deterministic pipeline the sole author of
every QA artifact. The agent gains a decision lifecycle; it gains no ability to
generate artifacts, execute a stage, or write a checkpoint.

## Decision

Add a bounded observe → decide → act loop around the existing single act. The
only genuinely new capability is deciding **whether another resume is
worthwhile** and looping accordingly. Everything the loop observes and every act
it performs already exists; nothing about how a single act runs changes.

### The loop

```
observe → plan → act (run, or resume if checkpoints exist)
  success:      observe → (clarification?) → reflect(goal_achieved) → Finish
  StageError:   observe → decide:
                  repeated failure at a stage        → recommend manual review, Finish
                  resume_attempts >= max             → stop, Finish
                  no completed stage to resume from   → stop, Finish
                  otherwise                           → resume, loop again
```

- **Observe** (`observe.py`): a read-only `Observation` snapshot — execution
  status, `completed_stages()`, `manifest()`, failed stage, repeated-failure
  flag, and coverage/gap/unresolved-condition metrics. It reads existing
  surfaces and writes nothing.
- **Decide** (`decide()` in `loop.py`): deterministic. Maps an Observation to a
  `LoopDecision` plus a structured `Decision` (decision / reason / alternative /
  rejected-because).
- **Act**: delegates to the unchanged `DesignService.run()` / `.resume()`. The
  loop never calls a stage.
- **Reflect**: the Phase-26 `Reflector`, extended with cumulative terminal
  signals (`goal_achieved`, `needs_clarification`, `needs_manual_review`).

### Terminal conditions

`COMPLETED` (a run/resume produced a full result), `MAX_RESUME_ATTEMPTS`
(`settings.max_resume_attempts` reached), `NEEDS_CLARIFICATION` (unresolved-
condition fraction or gap count over the existing thresholds), or
`NEEDS_MANUAL_REVIEW` (the same stage failed across attempts — stop rather than
loop forever).

### max_resume_attempts is a setting

Per the Phase-27 decision, the resume bound lives in `QAOpsSettings`
(`max_resume_attempts`, default 2). The agent decides whether another resume is
worthwhile; per-stage provider/model retry and failover remain owned by
`AdaptiveExecutor` and are untouched. The agent's "stop" is a decision not to
call `resume()` again — it never re-implements the executor's retry.

### The loop is the default execution path

Per the Phase-27 decision, the API runner now drives execution through
`OrchestratorAgent.execute_until_goal()` rather than calling the service
directly. This is safe because the agent remains a thin delegator: every act is
`DesignService.run()` / `.resume()`, unchanged. When the first act succeeds — the
common case, and always when there are no checkpoints — the loop runs exactly one
iteration and returns the same outcome Phase 26 would, with **byte-identical
artifacts** (asserted by a direct-vs-agent comparison test).

### Determinism and ownership

- **Artifacts:** produced only by the deterministic stages. The loop's models
  (`Observation`, `LoopSummary`, `Reflection`) have no field capable of holding a
  requirement, scenario, or test case; an act's result flows straight through.
- **Checkpoints:** `CheckpointStore` is unchanged. The loop only reads it
  (`completed_stages`, `manifest`); it never writes.
- **Retry/failover:** owned by `AdaptiveExecutor`. The loop decides only about
  resume attempts.

### Additive API/UI

A `loop_summary` (iterations with their observation + decision, terminal reason,
resume attempts, cumulative reflection) is added to the run-status response and
rendered as an optional panel. All fields are optional/defaulted; existing
endpoints, fields, and workflows are unchanged, and pre-loop runs simply have a
null `loop_summary`. Loop-summary population is best-effort and never blocks or
fails a run.

### Failure detail preserved

Because the loop catches `StageError` internally (to decide whether to resume),
it carries the underlying error, failed stage, and sanitized attempt history on
the `LoopSummary` so the API surfaces the real failure — run through the same
secret-redaction as before — not just the terminal reason.

## Consequences

- A transient late failure can now self-recover by resuming, up to the
  configured bound, without user intervention.
- Repeated or unrecoverable failures stop cleanly with a recorded reason and
  partial artifacts, rather than looping or losing work.
- The determinism and artifact-ownership guarantees of Phase 26 are preserved
  and re-asserted for the loop.

## What makes this agentic (beyond Phase 26)

Phase 26 planned and reflected around a single act. Phase 27 closes the loop: the
agent observes outcomes, decides among continue / resume / stop / recommend, acts
again, and reflects cumulatively — managing execution toward a goal across
multiple acts. It is autonomous within an explicit, deterministic decision policy
and a configured bound, and it still authors no artifact.

## Alternatives considered

- **Keep the loop opt-in:** rejected per the Phase-27 decision to make it the
  default; safe because the agent is a thin delegator and no-op runs are
  identical.
- **Let the agent retry stages itself:** rejected — retry ownership stays with
  `AdaptiveExecutor`; the agent only decides about resume.
- **Hardcode the resume bound:** rejected — moved to `QAOpsSettings` for
  configurability.

## Limitations / future work

The decision policy is deterministic and conservative (don't re-resume a stage
that already failed; stop at the bound). LLM-assisted decision reasoning, richer
goal interpretation, and per-stage resume targeting are candidate future work.
Restart-resilience across a server restart remains out of scope (ADR-040).
