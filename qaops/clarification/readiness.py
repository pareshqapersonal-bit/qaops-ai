"""Readiness calculation (Phase 41A).

A pure function: given the current clarification questions plus the count of
critical (blocker) gaps still open, derive whether the run may proceed to test
design. No IO, no LLM. The rule:

    ready  <=>  no unanswered BLOCKING questions AND no critical gaps.

Recommended and optional questions may remain unanswered (skipping them records an
assumption elsewhere); only blocking questions and blocker gaps gate readiness.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qaops.clarification.enums import QuestionPriority, QuestionStatus
from qaops.clarification.models import ReadinessStatus

if TYPE_CHECKING:
    from collections.abc import Sequence

    from qaops.clarification.models import ClarificationQuestion


def _unanswered(question: ClarificationQuestion) -> bool:
    return question.status is QuestionStatus.UNANSWERED


def compute_readiness(
    questions: Sequence[ClarificationQuestion],
    *,
    critical_gaps: int = 0,
    requirements_total: int = 0,
) -> ReadinessStatus:
    """Derive readiness from questions and open critical-gap count.

    A question counts as "unanswered" only while its status is UNANSWERED; ANSWERED
    and SKIPPED both clear it (skipping is a deliberate proceed-with-assumption).
    `critical_gaps` is the number of blocker-severity gaps still open, supplied by
    the caller (this function does not re-run gap analysis). The run is ready when
    there are zero unanswered blocking questions and zero critical gaps.
    """
    blocking_unanswered = sum(
        1 for q in questions if q.priority is QuestionPriority.BLOCKING and _unanswered(q)
    )
    recommended_unanswered = sum(
        1 for q in questions if q.priority is QuestionPriority.RECOMMENDED and _unanswered(q)
    )
    optional_unanswered = sum(
        1 for q in questions if q.priority is QuestionPriority.OPTIONAL and _unanswered(q)
    )

    reasons: list[str] = []
    if blocking_unanswered:
        reasons.append(f"{blocking_unanswered} blocking question(s) unanswered.")
    if critical_gaps:
        reasons.append(f"{critical_gaps} critical gap(s) still open.")

    ready = blocking_unanswered == 0 and critical_gaps == 0
    return ReadinessStatus(
        ready=ready,
        requirements_total=requirements_total,
        blocking_unanswered=blocking_unanswered,
        recommended_unanswered=recommended_unanswered,
        optional_unanswered=optional_unanswered,
        critical_gaps=critical_gaps,
        blocking_reasons=reasons,
    )
