"""Enumerations for the clarification layer (Phase 41A).

Kept separate from qaops.models.enums so the clarification feature is additive and
self-contained. QuestionPriority maps directly from the existing GapSeverity
(blocker/major/minor) so no change to the gap model is needed.
"""

from enum import StrEnum


class QuestionPriority(StrEnum):
    """How strongly an unanswered question blocks confident test design.

    Maps from GapSeverity: BLOCKER -> BLOCKING, MAJOR -> RECOMMENDED,
    MINOR -> OPTIONAL. A blocking question must be answered (or explicitly
    proceeded past with an assumption) before test design; recommended and
    optional may be skipped.
    """

    BLOCKING = "blocking"
    RECOMMENDED = "recommended"
    OPTIONAL = "optional"


class AnswerType(StrEnum):
    """The shape of answer a question expects.

    Drives both the UI widget (later phase) and how the stored answer is
    validated/rendered back into requirement text.
    """

    BOOLEAN = "boolean"
    SINGLE_SELECT = "single_select"
    MULTI_SELECT = "multi_select"
    NUMERIC = "numeric"
    DATE = "date"
    TEXT = "text"


class QuestionStatus(StrEnum):
    """Lifecycle of a single question within a clarification round."""

    UNANSWERED = "unanswered"
    ANSWERED = "answered"
    SKIPPED = "skipped"


class ClarificationStatus(StrEnum):
    """Lifecycle of the clarification process for a run.

    Additive to (not a replacement for) RunStatus; the run-status wiring is a
    later phase. Here it records where the clarification loop stands.
    """

    ANALYZING = "analyzing"
    CLARIFYING = "clarifying"
    RE_ANALYZING = "re_analyzing"
    READY_FOR_TEST_DESIGN = "ready_for_test_design"


class AssumptionSource(StrEnum):
    """Why an assumption was recorded."""

    USER_SKIP = "user_skip"
    AGENT_DEFAULT = "agent_default"
