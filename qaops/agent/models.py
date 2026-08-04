"""Domain models for the Orchestrator Agent (ADR-041, Phase 26).

These describe how the pipeline will execute and why - a plan, the decisions
behind it, and a post-run reflection. They are ORCHESTRATION artifacts: they
never contain requirements, business rules, gaps, scenarios, conditions, test
cases, or coverage. Those seven artifact types remain owned exclusively by the
deterministic pipeline stages; the agent only reasons about execution.

All models are Pydantic so they serialize cleanly into API responses and can be
validated when produced (partly) by the LLM.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class PlanStepStatus(StrEnum):
    """How the orchestrator intends to treat a stage in this execution."""

    # Will run now.
    RUN = "run"
    # Already completed in a prior attempt (checkpoint exists) - reuse, skip.
    REUSE = "reuse"


class PlanStep(BaseModel):
    """One stage in the execution plan, with the agent's reasoning for it.

    `reason`, `dependencies`, and `expected_output` explain WHY the stage is in
    the plan. They may be authored by the LLM (evidence-based) or filled from the
    deterministic stage metadata; either way they describe execution, not output.
    """

    order: int
    stage: str
    status: PlanStepStatus = PlanStepStatus.RUN
    reason: str = ""
    dependencies: list[str] = Field(default_factory=list)
    expected_output: str = ""


class Decision(BaseModel):
    """A recorded orchestration decision (resume vs restart, retry, etc.).

    Structured exactly as the spec requires: the decision, its reason, the
    alternative that was considered, and why that alternative was rejected.
    """

    decision: str
    reason: str
    alternative_considered: str = ""
    rejected_because: str = ""


class ExecutionPlan(BaseModel):
    """The ordered plan plus the decisions that produced it.

    `goal` is the human execution goal (e.g. "generate a complete regression
    pack"). `entry_point` and `resume` capture the two structural choices. The
    plan is built deterministically from the entry point and checkpoint state;
    the per-step prose reasoning may be enriched by the LLM.
    """

    goal: str
    entry_point: str
    resume: bool
    steps: list[PlanStep] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    # True when the plan runs the full pipeline from the start with no reuse -
    # i.e. the agent made no structural intervention. Used to assert Phase-25
    # identical behaviour.
    no_intervention: bool = True


class StageOutcome(BaseModel):
    """Per-stage execution outcome, derived from the run's manifest/history."""

    stage: str
    status: str
    retried: bool = False
    recovered: bool = False
    skipped: bool = False


class Reflection(BaseModel):
    """Post-execution reasoning about how the run went (ADR-041).

    Reasoning ONLY. It reads the produced result and execution metadata and never
    regenerates any pipeline artifact. The `recommendations` may include a
    clarification recommendation when the deterministic ambiguity signal
    (unresolved conditions / gaps) exceeds a threshold.
    """

    summary: str
    successes: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    retries: list[str] = Field(default_factory=list)
    recovered_stages: list[str] = Field(default_factory=list)
    skipped_stages: list[str] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    stage_outcomes: list[StageOutcome] = Field(default_factory=list)
    # Phase 27 (ADR-042), additive terminal signals for the goal-driven loop.
    # Defaulted so a Phase-26 single-shot reflection is unaffected.
    goal_achieved: bool = False
    needs_clarification: bool = False
    needs_manual_review: bool = False


class LoopDecision(StrEnum):
    """The agent's decision after observing the outcome of one act (ADR-042)."""

    CONTINUE = "continue"  # run/resume produced a complete result -> finish
    RESUME = "resume"  # a stage failed with completed work behind it -> resume
    STOP = "stop"  # give up (bound reached or nothing to resume)
    RECOMMEND_CLARIFICATION = "recommend_clarification"
    RECOMMEND_MANUAL_REVIEW = "recommend_manual_review"


class TerminalReason(StrEnum):
    """Why the goal-driven loop ended (ADR-042)."""

    COMPLETED = "completed"  # pipeline finished successfully
    MAX_RESUME_ATTEMPTS = "max_resume_attempts"  # bound reached
    NEEDS_CLARIFICATION = "needs_clarification"  # ambiguity over threshold
    NEEDS_MANUAL_REVIEW = "needs_manual_review"  # repeated failure at a stage


class Observation(BaseModel):
    """A read-only snapshot of execution state the loop decides on (ADR-042).

    Purely observational: every field is read from existing surfaces
    (CheckpointStore, the last outcome/error, coverage metrics). The agent never
    mutates any of these; Observation is the input to a Decision, nothing more.
    """

    iteration: int
    resume_attempts: int
    succeeded: bool
    completed_stages: list[str] = Field(default_factory=list)
    failed_stage: str | None = None
    repeated_failure: bool = False
    unresolved_conditions: int = 0
    total_conditions: int = 0
    gap_count: int = 0


class LoopIteration(BaseModel):
    """One observe -> decide -> act cycle, recorded for transparency (ADR-042)."""

    iteration: int
    observation: Observation
    decision: Decision
    acted: bool  # whether an act (run/resume) followed this decision


class LoopSummary(BaseModel):
    """The full record of a goal-driven execution loop (ADR-042).

    Additive transparency artifact: the ordered iterations, the terminal reason,
    and the cumulative reflection. It contains reasoning about execution only -
    never any pipeline artifact.
    """

    goal: str
    iterations: list[LoopIteration] = Field(default_factory=list)
    terminal_reason: str
    resume_attempts: int = 0
    reflection: Reflection
    # When the loop ended without a complete result, these carry the underlying
    # failure so the API can surface the real error/stage (not just the terminal
    # reason). None on a successful run.
    last_error: str | None = None
    last_failed_stage: str | None = None
    last_attempts: list[dict[str, object]] = Field(default_factory=list)
