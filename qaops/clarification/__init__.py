"""Interactive requirement clarification (Phase 41).

Phase 41A provides the pure state layer only: the clarification domain models,
deterministic persistence to the run workspace, and a pure readiness calculation.
No LLM, API, frontend, or pipeline wiring lives here yet - those are later phases
(41B agent, 41C API, 41D frontend). This module consumes the existing GapReport
and hands off (eventually) to the existing pipeline; it does not replace GapAnalyzer.
"""

from qaops.clarification.agent import ClarificationAgent
from qaops.clarification.enums import (
    AnswerType,
    ClarificationStatus,
    QuestionPriority,
    QuestionStatus,
)
from qaops.clarification.models import (
    Assumption,
    ClarificationAnswer,
    ClarificationQuestion,
    ClarificationState,
    ReadinessStatus,
)
from qaops.clarification.readiness import compute_readiness
from qaops.clarification.state_store import (
    ClarificationStateError,
    clarification_state_path,
    load_clarification_state,
    write_clarification_state,
)

__all__ = [
    "AnswerType",
    "Assumption",
    "ClarificationAgent",
    "ClarificationAnswer",
    "ClarificationQuestion",
    "ClarificationState",
    "ClarificationStateError",
    "ClarificationStatus",
    "QuestionPriority",
    "QuestionStatus",
    "ReadinessStatus",
    "clarification_state_path",
    "compute_readiness",
    "load_clarification_state",
    "write_clarification_state",
]
