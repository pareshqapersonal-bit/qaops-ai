"""Clarification domain models (Phase 41A).

Pure data models for the human-in-the-loop clarification layer. They consume the
existing Gap/Requirement models (via references, not by modifying them) and are
persisted to the run workspace by state_store. No LLM or IO logic lives here.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from qaops.clarification.enums import (
    AnswerType,
    AssumptionSource,
    ClarificationStatus,
    QuestionPriority,
    QuestionStatus,
)


class _Strict(BaseModel):
    """Clarification base: forbid unknown fields, validate on assignment.

    Mirrors qaops.models.domain._StrictModel without importing it, keeping the
    clarification package self-contained and additive.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ClarificationQuestion(_Strict):
    """One question posed to the BA/PO to close a requirement gap.

    Derived (in a later phase) from an existing Gap: `gap_reference` links back to
    the gap's description, `requirement_id` to the requirement it clarifies, and
    `priority` maps from the gap's severity. Options are populated for select-type
    answers. `evidence` carries verbatim source excerpts that motivated the question.
    """

    question_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    priority: QuestionPriority = QuestionPriority.RECOMMENDED
    answer_type: AnswerType = AnswerType.TEXT
    requirement_id: str | None = None
    gap_reference: str = Field(
        default="",
        description="The gap description this question was generated from.",
    )
    options: list[str] = Field(default_factory=list)
    reason: str = Field(
        default="",
        description="Why answering this improves test-design confidence.",
    )
    evidence: list[str] = Field(default_factory=list)
    status: QuestionStatus = QuestionStatus.UNANSWERED


class ClarificationAnswer(_Strict):
    """A structured answer to one question.

    `answer` is stored as text for portability (a boolean is 'true'/'false', a
    numeric is its string form, multi-select is a joined/serialized form decided by
    a later phase); `answer_type` records how to interpret it. Keeping answers
    structured - not free prose - is what lets re-analysis fold them back cleanly.
    """

    question_id: str = Field(min_length=1)
    answer_type: AnswerType
    answer: str = Field(min_length=1)
    answered_at: datetime | None = None


class Assumption(_Strict):
    """A recorded assumption for a skipped/unanswerable question.

    When a recommended/optional question is skipped, the clarification layer records
    the assumption test design will proceed under, preserving traceability. Flows
    (in a later phase) into the requirement's existing `assumptions` field.
    """

    assumption_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    requirement_id: str | None = None
    question_id: str | None = None
    source: AssumptionSource = AssumptionSource.USER_SKIP


class ReadinessStatus(_Strict):
    """Computed readiness of a run to proceed to test design.

    Pure derivation from the current questions/answers/gaps - never sets itself;
    compute_readiness() produces it. `ready` is the gate the (later) API uses.
    """

    ready: bool = False
    requirements_total: int = 0
    blocking_unanswered: int = 0
    recommended_unanswered: int = 0
    optional_unanswered: int = 0
    critical_gaps: int = 0
    blocking_reasons: list[str] = Field(default_factory=list)


class ClarificationState(_Strict):
    """The persisted aggregate for a run's clarification process.

    Written to and read from the run workspace by state_store. `iteration` versions
    the question batch so a stale answer (submitted after re-analysis) can be
    detected. This is the single source of truth for where clarification stands.
    """

    run_id: str = Field(min_length=1)
    iteration: int = Field(default=0, ge=0)
    status: ClarificationStatus = ClarificationStatus.ANALYZING
    questions: list[ClarificationQuestion] = Field(default_factory=list)
    answers: list[ClarificationAnswer] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    readiness: ReadinessStatus = Field(default_factory=ReadinessStatus)
    # Phase 41E-1, additive (default empty so pre-41E persisted state still loads):
    # the signatures of gaps already turned into questions across all iterations,
    # so a later 41E phase can suppress duplicate questions when gap analysis is
    # re-run. A list (not a set) to stay JSON-serialisable and deterministic; order
    # is insertion order. No behaviour reads or writes this yet - 41E-1 only defines
    # the field.
    asked_gap_signatures: list[str] = Field(default_factory=list)
