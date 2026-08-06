# ADR-043: Multi-agent supervisor refactor

**Status:** Accepted · **Date:** 2026-08-05 · **Relates to:** ADR-041 (orchestrator agent), ADR-042 (goal-driven loop)

## Context

Phases 26–27 built a single `OrchestratorAgent` that planned, delegated
execution, ran the goal-driven loop, and reflected. Internally it already held
three collaborators (`ExecutionPlanner`, the delegation to `DesignService`, and
`Reflector`) plus the `GoalDrivenLoop`, but they were wired together inside one
class. As more agents arrive in future phases, one monolith coordinating
everything would not scale cleanly.

Phase 28 is a **pure structural refactor**: decompose the monolith into a
supervisor coordinating three specialized agents, with **100% identical external
behavior**. It introduces zero new QA functionality. The success criterion is a
cleaner architecture that is open to future agents, not new capability.

## Decision

Decompose `OrchestratorAgent` into:

- **`PlanningAgent`** (`agent/agents/planning.py`) — owns planning; wraps the
  unchanged `ExecutionPlanner`.
- **`ExecutionAgent`** (`agent/agents/execution.py`) — the single place the agent
  layer touches execution; delegates only to `DesignService.run()`/`.resume()`
  and never executes a stage.
- **`ReflectionAgent`** (`agent/agents/reflection.py`) — owns reflection; wraps
  the unchanged `Reflector`.
- **`SupervisorAgent`** (`agent/supervisor.py`) — composes the three agents and
  drives the `GoalDrivenLoop`; the coordination layer the runner talks to.
- **`OrchestratorAgent`** (`agent/orchestrator.py`) — kept as a thin
  backward-compatible facade delegating to the supervisor, so existing callers
  and tests continue to work unchanged.

The architecture becomes:

```
Runner -> SupervisorAgent -> { PlanningAgent, ExecutionAgent, ReflectionAgent }
                                   -> GoalDrivenLoop (observe / decide / act / repeat)
```

### `observe()` and `decide()` stay utility functions

They are already deterministic and stateless. Wrapping each in its own agent
would add abstraction without value and risk behavioral drift, so they remain
plain functions used by the loop. Exactly three specialized agents are
introduced — no `ObserveAgent`, no `DecisionAgent`.

### `GoalDrivenLoop` is kept as a reusable engine

Its logic is not moved into the supervisor. The only change is that it now drives
acts through the `ExecutionAgent` instead of holding the `DesignService`
directly. Because `ExecutionAgent.run()/.resume()` are pass-throughs to
`DesignService.run()/.resume()`, this is byte-identical. Ownership is preserved:
the supervisor coordinates, the loop owns the execution cycle, the ExecutionAgent
delegates, and `DesignService`/`AdaptiveExecutor`/`CheckpointStore` keep their
existing responsibilities.

### Module layout

```
qaops/agent/
    base.py            # Agent ABC (unchanged)
    supervisor.py      # SupervisorAgent
    orchestrator.py    # backward-compatible facade
    agents/
        planning.py
        execution.py
        reflection.py
    loop.py  observe.py  planner.py  reflection.py  models.py
```

This is open for future agents: a new agent is added under `agents/` and
registered with the supervisor, without modifying existing agents.

## Required verification (behavioral identity)

The primary success criterion is that Phase 28 is observably identical to
Phase 27. Proven by:

- **Existing suite unchanged:** all 780 Phase 25–27 tests pass without
  modification — including the tests that construct `OrchestratorAgent` directly
  and the direct-vs-agent artifact-identity tests. That is the identity
  guarantee.
- **Explicit comparison tests** (Phase 28 suite): SupervisorAgent vs the
  OrchestratorAgent facade, and SupervisorAgent vs direct `DesignService`,
  produce byte-identical artifacts, identical checkpoints, and identical loop
  summaries (terminal reason, iterations, full structure).
- **API/UI:** no schema change; the runner returns the same
  `(plan, outcome, loop_summary)`. The frontend test suite passes untouched,
  confirming identical API responses.

## Preserved guarantees

Pipeline remains the sole artifact generator; `ExecutionAgent` delegates only to
`DesignService.run()`/`.resume()` and never executes a stage; `AdaptiveExecutor`
still owns retry/failover; `CheckpointStore` still owns checkpoints (the agent
layer reads only); `DesignService` remains the execution owner; deterministic
execution and backward compatibility are intact.

## Consequences

- Cleaner separation: each agent has one responsibility, and the supervisor's
  wiring is explicit and small.
- A stable extension point for future agents without touching existing ones.
- The `OrchestratorAgent` name continues to work for any external caller.

## Alternatives considered

- **Five agents (adding Observe/Decision agents):** rejected — over-abstraction
  of stateless functions, with drift risk and no benefit.
- **Move the loop into the supervisor:** rejected — keeping `GoalDrivenLoop` as a
  reusable engine preserves separation and minimizes code motion (lower identity
  risk).
- **Remove `OrchestratorAgent`:** rejected — breaking change; the facade keeps
  compatibility at negligible cost.

## Limitations / future work

No behavioral change by design. Future phases can add agents (e.g. triage,
prioritization) behind the supervisor and, if useful, a registry for dynamic
agent routing. The in-process scope from ADR-040/042 is unchanged.
