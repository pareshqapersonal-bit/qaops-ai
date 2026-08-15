"""ClarificationAgent (Phase 41B).

The intelligence layer over the existing GapAnalyzer. It:

1. turns an existing GapReport into a small batch (3-7) of answerable
   ClarificationQuestions, preferring yes/no, then select, then free text;
2. applies the user's structured answers by AUGMENTING the affected requirements
   (never regenerating them - IDs and original content are preserved) and recording
   Assumptions for skipped questions;
3. recomputes readiness via the existing Phase 41A compute_readiness().

It reuses the pipeline's structured-output seam (run_structured_stage) and is
text-only: no images are ever passed to it. It does not modify GapAnalyzer, the
Phase 41A models, structured.py, or any provider code.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from qaops.clarification.enums import (
    AssumptionSource,
    QuestionPriority,
    QuestionStatus,
)
from qaops.clarification.models import (
    Assumption,
    ClarificationAnswer,
    ClarificationQuestion,
    ClarificationState,
)
from qaops.clarification.readiness import compute_readiness
from qaops.clarification.schemas import ShapedQuestionBatch
from qaops.models.enums import GapSeverity
from qaops.pipelines.test_design._support import run_structured_stage

if TYPE_CHECKING:
    from collections.abc import Sequence

    from qaops.config import QAOpsSettings
    from qaops.llm import LLMClient, PromptLoader
    from qaops.models.domain import Gap, GapReport, Requirement

PROMPT_NAME = "clarification_agent"

# Deterministic severity -> priority map (never delegated to the LLM).
_SEVERITY_TO_PRIORITY = {
    GapSeverity.BLOCKER: QuestionPriority.BLOCKING,
    GapSeverity.MAJOR: QuestionPriority.RECOMMENDED,
    GapSeverity.MINOR: QuestionPriority.OPTIONAL,
}

# Default and hard ceiling for a single question batch.
_MAX_BATCH = 7


class ClarificationAgent:
    """Generates clarification questions and applies structured answers."""

    def __init__(self, client: LLMClient, prompts: PromptLoader, settings: QAOpsSettings) -> None:
        self._client = client
        self._prompts = prompts
        self._settings = settings

    @classmethod
    def for_answer_processing(cls) -> ClarificationAgent:
        """Construct an agent for the pure answer-processing methods only.

        apply_answers, mark_skipped, record_skip_assumption, and refresh_readiness
        never call the LLM, so a caller that only processes answers needs no client.
        generate_questions must NOT be called on such an instance.
        """
        return cls.__new__(cls)

    # -- Question generation --------------------------------------------------

    def generate_questions(
        self,
        requirements: Sequence[Requirement],
        gap_report: GapReport,
        *,
        already_asked: Sequence[ClarificationQuestion] = (),
        max_batch: int = _MAX_BATCH,
    ) -> list[ClarificationQuestion]:
        """Turn unresolved gaps into a small, blocking-first question batch.

        Severity -> priority and requirement linkage come deterministically from
        each Gap; the LLM only shapes phrasing/answer-type/options and may skip a
        gap whose answer is already known. Questions duplicating ones already asked
        (same requirement + normalized text) are suppressed. The batch is ordered
        blocking-first and capped at max_batch (<= 7); it is never padded.
        """
        gaps = list(gap_report.gaps)
        if not gaps:
            return []

        batch = run_structured_stage(
            client=self._client,
            prompts=self._prompts,
            settings=self._settings,
            prompt_name=PROMPT_NAME,
            schema=ShapedQuestionBatch,
            requirements_json=_requirements_as_json(requirements),
            gaps_json=_gaps_as_json(gaps),
        )

        asked_keys = {_dedupe_key(q.requirement_id, q.question) for q in already_asked}
        questions: list[ClarificationQuestion] = []
        seen_keys: set[str] = set()
        for shaped in batch.questions:
            if shaped.skip:
                continue
            if not (0 <= shaped.gap_index < len(gaps)):
                continue  # model referenced a gap that doesn't exist; ignore it
            gap = gaps[shaped.gap_index]
            text = shaped.question.strip() or gap.suggested_question.strip() or gap.description
            key = _dedupe_key(gap.requirement_id, text)
            if key in asked_keys or key in seen_keys:
                continue  # duplicate of a prior iteration or within this batch
            seen_keys.add(key)
            questions.append(
                ClarificationQuestion(
                    question_id=f"Q-{len(questions) + 1:03d}",
                    question=text,
                    priority=_SEVERITY_TO_PRIORITY.get(gap.severity, QuestionPriority.RECOMMENDED),
                    answer_type=shaped.answer_type,
                    requirement_id=gap.requirement_id,
                    gap_reference=gap.description,
                    options=list(shaped.options),
                    reason=shaped.reason.strip(),
                    evidence=[gap.description],
                )
            )

        # Blocking first, then recommended, then optional; stable within a group.
        order = {
            QuestionPriority.BLOCKING: 0,
            QuestionPriority.RECOMMENDED: 1,
            QuestionPriority.OPTIONAL: 2,
        }
        questions.sort(key=lambda q: order[q.priority])
        capped = questions[: max(1, min(max_batch, _MAX_BATCH))] if questions else []
        # Renumber after sort/cap so IDs are contiguous within the batch.
        for i, q in enumerate(capped, start=1):
            q.question_id = f"Q-{i:03d}"
        return capped

    # -- Answer application ---------------------------------------------------

    def apply_answers(
        self,
        requirements: Sequence[Requirement],
        questions: Sequence[ClarificationQuestion],
        answers: Sequence[ClarificationAnswer],
    ) -> tuple[list[Requirement], list[ClarificationQuestion]]:
        """Augment requirements with answers; never regenerate them.

        For each answer, the matching question's requirement gets an appended
        clarification line in its `assumptions` list (an audit-friendly augmentation
        that preserves the original description and the requirement id). The question
        is marked ANSWERED. Returns new Requirement copies (inputs are not mutated)
        and the updated questions. Contradictory answers for the same question are
        rejected (see _detect_contradiction) by keeping the first and flagging.
        """
        by_id = {q.question_id: q for q in questions}
        # Latest-wins would hide contradictions; instead keep first and detect.
        resolved: dict[str, ClarificationAnswer] = {}
        for a in answers:
            if a.question_id in resolved and _values_conflict(resolved[a.question_id], a):
                msg = (
                    f"Contradictory answers for {a.question_id}: "
                    f"{resolved[a.question_id].answer!r} vs {a.answer!r}"
                )
                raise ValueError(msg)
            resolved.setdefault(a.question_id, a)

        # Group clarifications by requirement id.
        additions: dict[str | None, list[str]] = {}
        updated_questions = [q.model_copy(deep=True) for q in questions]
        q_by_id = {q.question_id: q for q in updated_questions}
        for qid, answer in resolved.items():
            question = by_id.get(qid)
            if question is None:
                continue
            line = f"Clarification ({question.question}) -> {answer.answer}"
            additions.setdefault(question.requirement_id, []).append(line)
            q_by_id[qid].status = QuestionStatus.ANSWERED

        new_reqs: list[Requirement] = []
        for req in requirements:
            extra = additions.get(req.id, [])
            if extra:
                new_reqs.append(req.model_copy(update={"assumptions": [*req.assumptions, *extra]}))
            else:
                new_reqs.append(req.model_copy(deep=True))
        return new_reqs, updated_questions

    # -- Assumptions for skipped questions ------------------------------------

    def record_skip_assumption(self, question: ClarificationQuestion) -> Assumption:
        """Create an explicit Assumption for a skipped question (traceable)."""
        return Assumption(
            assumption_id=f"ASM-{question.question_id}",
            text=(
                f"{question.question} was not answered; test design proceeds with the "
                f"behavior unspecified."
            ),
            requirement_id=question.requirement_id,
            question_id=question.question_id,
            source=AssumptionSource.USER_SKIP,
        )

    def mark_skipped(
        self, questions: Sequence[ClarificationQuestion]
    ) -> tuple[list[ClarificationQuestion], list[Assumption]]:
        """Mark unanswered questions SKIPPED and produce their assumptions."""
        out: list[ClarificationQuestion] = []
        assumptions: list[Assumption] = []
        for q in questions:
            if q.status is QuestionStatus.UNANSWERED:
                copy = q.model_copy(update={"status": QuestionStatus.SKIPPED})
                out.append(copy)
                assumptions.append(self.record_skip_assumption(copy))
            else:
                out.append(q.model_copy(deep=True))
        return out, assumptions

    # -- Readiness (delegates to the single Phase 41A implementation) ---------

    def refresh_readiness(
        self, state: ClarificationState, *, critical_gaps: int, requirements_total: int
    ) -> ClarificationState:
        """Recompute readiness via the existing compute_readiness and return a copy."""
        readiness = compute_readiness(
            state.questions,
            critical_gaps=critical_gaps,
            requirements_total=requirements_total,
        )
        return state.model_copy(update={"readiness": readiness})


# -- Prompt serialization helpers ---------------------------------------------


def _requirements_as_json(requirements: Sequence[Requirement]) -> str:
    return json.dumps(
        [{"id": r.id, "title": r.title, "description": r.description} for r in requirements],
        indent=2,
    )


def _gaps_as_json(gaps: Sequence[Gap]) -> str:
    return json.dumps(
        [
            {
                "gap_index": i,
                "description": g.description,
                "requirement_id": g.requirement_id,
                "severity": g.severity.value,
                "suggested_question": g.suggested_question,
            }
            for i, g in enumerate(gaps)
        ],
        indent=2,
    )


def _dedupe_key(requirement_id: str | None, text: str) -> str:
    return f"{requirement_id or ''}::{' '.join(text.lower().split())}"


def _values_conflict(a: ClarificationAnswer, b: ClarificationAnswer) -> bool:
    return a.answer.strip().casefold() != b.answer.strip().casefold()
