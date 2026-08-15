"""HTTP response schemas (ADR-028).

Thin Pydantic models describing what the API returns. They are the API's
contract, deliberately separate from the domain models so an internal change
does not silently reshape the HTTP surface. None of them carry secrets.
"""

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class ModelSchema(BaseModel):
    id: str
    max_context_tokens: int
    max_output_tokens: int
    structured_output: bool
    local: bool
    free: bool


class ProviderModelsSchema(BaseModel):
    provider: str
    source: str  # "live", "static", or "cache"
    models: list[ModelSchema]


class ModelsResponse(BaseModel):
    providers: list[ProviderModelsSchema]


class RunCreatedResponse(BaseModel):
    run_id: str
    status: str


class TicketRequest(BaseModel):
    """A Jira-style ticket accepted by POST /api/v1/design/ticket (Phase 32).

    A request-only model: it validates the incoming ticket and is transcribed to
    Markdown by the TicketNormalizer, which then enters the existing DOCUMENT
    pipeline. It is deliberately NOT a domain/pipeline model - the pipeline input
    remains RequirementInput. Missing optional detail is left absent so the
    existing gap analysis surfaces it; nothing here is fabricated.
    """

    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    # Empty list is allowed: a ticket with no acceptance criteria is a genuine
    # sparse ticket that must produce gaps, not a validation error.
    acceptance_criteria: list[str] = Field(default_factory=list)
    ticket_id: str | None = None
    priority: str | None = None
    labels: list[str] = Field(default_factory=list)


class SummarySchema(BaseModel):
    requirements: int
    business_rules: int
    scenarios: int
    test_conditions: int = 0
    test_cases: int
    gaps: int
    # Backward-compatible headline (equals requirement coverage); not a claim of
    # exhaustive testing. The per-dimension fields tell the fuller story (ADR-036).
    coverage_percent: float
    requirement_coverage_percent: float = 0.0
    business_rule_coverage_percent: float = 0.0
    scenario_coverage_percent: float = 0.0
    condition_coverage_percent: float = 0.0
    unresolved_conditions: int = 0
    expansion_truncated: bool = False


class ProgressSchema(BaseModel):
    current_stage: str | None = None
    stage_index: int = 0
    stage_count: int = 0
    provider: str | None = None
    model: str | None = None
    # Disambiguated counters (ADR-030). model_attempt_number is which distinct
    # model this is for the stage; request_attempt is which network request for
    # the current model. models_attempted is retained for compatibility.
    model_attempt_number: int = 0
    request_attempt: int = 0
    # Actual provider generation calls for the stage so far (ADR-030), the
    # honest count including structured-output repair calls.
    provider_call_number: int = 0
    models_attempted: int = 0
    recovery_attempts: int = 0
    message: str = ""


class AttemptSchema(BaseModel):
    """One sanitized failed attempt in the failure history (ADR-035).

    Normalized fields only - no keys, headers, payloads, or raw exception text.
    """

    stage: str
    provider: str
    model: str
    failure_kind: str
    status_code: int | None = None
    error_code: str | None = None


class StageStatusSchema(BaseModel):
    """Per-stage execution status for the run screen (ADR-040)."""

    stage: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None


class PlanStepSchema(BaseModel):
    """One stage in the agent's execution plan (ADR-041)."""

    order: int
    stage: str
    status: str
    reason: str = ""
    dependencies: list[str] = []
    expected_output: str = ""


class DecisionSchema(BaseModel):
    """One recorded orchestration decision (ADR-041)."""

    decision: str
    reason: str
    alternative_considered: str = ""
    rejected_because: str = ""


class ExecutionPlanSchema(BaseModel):
    """The agent's execution plan and the decisions behind it (ADR-041)."""

    goal: str
    entry_point: str
    resume: bool
    no_intervention: bool
    steps: list[PlanStepSchema] = []
    decisions: list[DecisionSchema] = []


class StageOutcomeSchema(BaseModel):
    stage: str
    status: str
    retried: bool = False
    recovered: bool = False
    skipped: bool = False


class ReflectionSchema(BaseModel):
    """The agent's post-execution reflection (ADR-041, reasoning only)."""

    summary: str
    successes: list[str] = []
    failures: list[str] = []
    retries: list[str] = []
    recovered_stages: list[str] = []
    skipped_stages: list[str] = []
    lessons: list[str] = []
    recommendations: list[str] = []
    stage_outcomes: list[StageOutcomeSchema] = []
    # Phase 27 (ADR-042) terminal signals, additive/defaulted.
    goal_achieved: bool = False
    needs_clarification: bool = False
    needs_manual_review: bool = False


class ObservationSchema(BaseModel):
    iteration: int
    resume_attempts: int
    succeeded: bool
    completed_stages: list[str] = []
    failed_stage: str | None = None
    repeated_failure: bool = False
    unresolved_conditions: int = 0
    total_conditions: int = 0
    gap_count: int = 0


class LoopIterationSchema(BaseModel):
    iteration: int
    observation: ObservationSchema
    decision: DecisionSchema
    acted: bool


class LoopSummarySchema(BaseModel):
    """The goal-driven loop's record: iterations, decisions, terminal reason
    (ADR-042). Reasoning about execution only - never a pipeline artifact."""

    goal: str
    iterations: list[LoopIterationSchema] = []
    terminal_reason: str
    resume_attempts: int = 0
    reflection: ReflectionSchema


class ReviewFindingSchema(BaseModel):
    """One deterministic quality-review finding (ADR-045, advisory)."""

    code: str
    severity: str
    category: str
    message: str
    references: list[str] = []
    recommendation: str = ""


class ReviewReportSchema(BaseModel):
    """The QualityReviewer's advisory report (ADR-045, Phase 30).

    Deterministic and read-only; present on COMPLETED runs only. Its presence or
    contents never change the run status - it is advisory.
    """

    source_name: str = ""
    findings: list[ReviewFindingSchema] = []
    observations: list[str] = []
    recommendations: list[str] = []


class ReviewAdviceItemSchema(BaseModel):
    """One prioritized explanation from the ReviewAgent (ADR-046, advisory)."""

    code: str
    severity: str
    explanation: str
    references: list[str] = []


class ReviewAdviceSchema(BaseModel):
    """The ReviewAgent's advisory narrative over the ReviewReport (ADR-046).

    Present only when review_advice_enabled and the run COMPLETED. Advisory: it
    explains the deterministic findings and never changes run status. generated_by
    records provenance ("deterministic" | "llm").
    """

    source_name: str = ""
    headline: str = ""
    items: list[ReviewAdviceItemSchema] = []
    recommendations: list[str] = []
    generated_by: str = "deterministic"


class RunStatusResponse(BaseModel):
    run_id: str
    status: str
    entry_point: str | None = None
    detection: str | None = None
    summary: SummarySchema | None = None
    progress: ProgressSchema | None = None
    error: str | None = None
    failed_stage: str | None = None
    recovery_attempts: int | None = None
    # Ordered sanitized history of failed attempts across providers/models, so a
    # failed run shows the whole failover story, not only the last error.
    attempt_history: list[AttemptSchema] | None = None
    # Phase 25 (ADR-040), all additive with defaults so existing clients are
    # unaffected: per-stage statuses, whether the run can be resumed, and the
    # names of stages that completed (for partial-progress display).
    stage_statuses: list[StageStatusSchema] | None = None
    resumable: bool = False
    completed_stages: list[str] | None = None
    # Phase 26 (ADR-041), additive: the orchestrator agent's plan and reflection.
    plan: ExecutionPlanSchema | None = None
    reflection: ReflectionSchema | None = None
    # Phase 27 (ADR-042), additive: the goal-driven loop's summary.
    loop_summary: LoopSummarySchema | None = None
    # Phase 30 (ADR-045), additive: the deterministic quality review (COMPLETED
    # runs only). Advisory - defaulted to None so existing clients are unaffected.
    review: ReviewReportSchema | None = None
    # Phase 31 (ADR-046), additive: the advisory ReviewAgent narrative. Present
    # only when review_advice_enabled and COMPLETED. Defaulted None (backward compat).
    review_advice: ReviewAdviceSchema | None = None


class ArtifactSchema(BaseModel):
    name: str
    format: str


class ArtifactsResponse(BaseModel):
    run_id: str
    artifacts: list[ArtifactSchema]


class ErrorResponse(BaseModel):
    detail: str


# Phase 41C (clarification), additive. Present only for clarification-enabled runs;
# one-shot runs never expose these endpoints, so existing clients are unaffected.
class ClarificationQuestionSchema(BaseModel):
    question_id: str
    question: str
    priority: str
    answer_type: str
    requirement_id: str | None = None
    options: list[str] = Field(default_factory=list)
    reason: str = ""


class ReadinessSchema(BaseModel):
    ready: bool
    requirements_total: int
    blocking_unanswered: int
    recommended_unanswered: int
    optional_unanswered: int
    critical_gaps: int
    blocking_reasons: list[str] = Field(default_factory=list)


class ClarificationResponse(BaseModel):
    run_id: str
    iteration: int
    status: str
    questions: list[ClarificationQuestionSchema]
    readiness: ReadinessSchema


class AnswerSchema(BaseModel):
    question_id: str
    answer_type: str
    answer: str


class AnswersRequest(BaseModel):
    answers: list[AnswerSchema] = Field(default_factory=list)
    proceed_with_assumptions: bool = False
