"""Phase 41A tests: clarification state model, persistence, and readiness.

Pure state layer only - no LLM, API, frontend, or pipeline. Covers the domain
models' strictness, the readiness calculation across blocking/recommended/optional
and critical-gap combinations, and deterministic workspace persistence with the
absent -> None and corrupt -> error contract mirroring the Phase 36B sidecar.
"""

from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from qaops.clarification import (
    AnswerType,
    Assumption,
    ClarificationAnswer,
    ClarificationQuestion,
    ClarificationState,
    ClarificationStateError,
    ClarificationStatus,
    QuestionPriority,
    QuestionStatus,
    ReadinessStatus,
    clarification_state_path,
    compute_readiness,
    load_clarification_state,
    write_clarification_state,
)


def _q(qid: str, priority: QuestionPriority, status: QuestionStatus = QuestionStatus.UNANSWERED):
    return ClarificationQuestion(
        question_id=qid, question=f"Question {qid}?", priority=priority, status=status
    )


# -- Models -------------------------------------------------------------------


class TestModels:
    def test_question_defaults(self) -> None:
        q = ClarificationQuestion(question_id="Q1", question="Which flow?")
        assert q.priority is QuestionPriority.RECOMMENDED
        assert q.answer_type is AnswerType.TEXT
        assert q.status is QuestionStatus.UNANSWERED
        assert q.options == []

    def test_question_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            ClarificationQuestion(question_id="Q1", question="x", bogus="y")

    def test_question_rejects_empty_id(self) -> None:
        with pytest.raises(ValidationError):
            ClarificationQuestion(question_id="", question="x")

    def test_answer_stores_typed_value(self) -> None:
        a = ClarificationAnswer(
            question_id="Q1",
            answer_type=AnswerType.BOOLEAN,
            answer="true",
            answered_at=datetime(2026, 8, 14, 12, 0, 0),
        )
        assert a.answer == "true"
        assert a.answer_type is AnswerType.BOOLEAN

    def test_assumption_defaults(self) -> None:
        asm = Assumption(assumption_id="A1", text="Assume the popup shows first.")
        assert asm.requirement_id is None
        assert asm.question_id is None

    def test_state_defaults(self) -> None:
        state = ClarificationState(run_id="run_1")
        assert state.iteration == 0
        assert state.status is ClarificationStatus.ANALYZING
        assert state.questions == []
        assert isinstance(state.readiness, ReadinessStatus)


# -- Readiness ----------------------------------------------------------------


class TestReadiness:
    def test_blocking_unanswered_not_ready(self) -> None:
        r = compute_readiness([_q("Q1", QuestionPriority.BLOCKING)])
        assert r.ready is False
        assert r.blocking_unanswered == 1
        assert r.blocking_reasons

    def test_all_blocking_answered_ready(self) -> None:
        r = compute_readiness(
            [_q("Q1", QuestionPriority.BLOCKING, QuestionStatus.ANSWERED)],
            requirements_total=5,
        )
        assert r.ready is True
        assert r.requirements_total == 5

    def test_recommended_unanswered_still_ready(self) -> None:
        # Only blocking questions gate readiness; recommended may remain open.
        r = compute_readiness(
            [
                _q("Q1", QuestionPriority.BLOCKING, QuestionStatus.ANSWERED),
                _q("Q2", QuestionPriority.RECOMMENDED),
                _q("Q3", QuestionPriority.OPTIONAL),
            ]
        )
        assert r.ready is True
        assert r.recommended_unanswered == 1
        assert r.optional_unanswered == 1

    def test_skipped_blocking_clears_readiness(self) -> None:
        # Skipping is a deliberate proceed-with-assumption: it clears "unanswered".
        r = compute_readiness([_q("Q1", QuestionPriority.BLOCKING, QuestionStatus.SKIPPED)])
        assert r.ready is True
        assert r.blocking_unanswered == 0

    def test_critical_gap_blocks_readiness(self) -> None:
        r = compute_readiness([], critical_gaps=2)
        assert r.ready is False
        assert r.critical_gaps == 2
        assert any("critical gap" in reason for reason in r.blocking_reasons)

    def test_blocking_and_gap_both_reported(self) -> None:
        r = compute_readiness([_q("Q1", QuestionPriority.BLOCKING)], critical_gaps=1)
        assert r.ready is False
        assert len(r.blocking_reasons) == 2

    def test_empty_is_ready(self) -> None:
        r = compute_readiness([])
        assert r.ready is True
        assert r.blocking_reasons == []


# -- Persistence --------------------------------------------------------------


def _state() -> ClarificationState:
    return ClarificationState(
        run_id="run_x",
        iteration=1,
        status=ClarificationStatus.CLARIFYING,
        questions=[
            _q("Q1", QuestionPriority.BLOCKING, QuestionStatus.ANSWERED),
            _q("Q2", QuestionPriority.RECOMMENDED),
        ],
        answers=[
            ClarificationAnswer(question_id="Q1", answer_type=AnswerType.BOOLEAN, answer="true")
        ],
        assumptions=[
            Assumption(assumption_id="A1", text="Default assumed", requirement_id="REQ-001")
        ],
    )


class TestPersistence:
    def test_write_creates_state_file(self, tmp_path: Path) -> None:
        write_clarification_state(tmp_path, _state())
        path = clarification_state_path(tmp_path)
        assert path.exists()
        # Separate from input/ and output/ (single-input contract preserved).
        assert path.parent.name == "clarification"
        assert "input" not in path.parts and "output" not in path.parts

    def test_round_trip_identical(self, tmp_path: Path) -> None:
        original = _state()
        write_clarification_state(tmp_path, original)
        loaded = load_clarification_state(tmp_path)
        assert loaded == original

    def test_absent_returns_none(self, tmp_path: Path) -> None:
        assert load_clarification_state(tmp_path) is None

    def test_corrupt_raises(self, tmp_path: Path) -> None:
        path = clarification_state_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("{ not valid json", encoding="utf-8")
        with pytest.raises(ClarificationStateError):
            load_clarification_state(tmp_path)

    def test_malformed_state_raises(self, tmp_path: Path) -> None:
        path = clarification_state_path(tmp_path)
        path.parent.mkdir(parents=True)
        # Valid JSON, but not a valid ClarificationState (missing run_id).
        path.write_text('{"iteration": 1}', encoding="utf-8")
        with pytest.raises(ClarificationStateError):
            load_clarification_state(tmp_path)

    def test_deterministic_bytes(self, tmp_path: Path) -> None:
        # Sorted keys -> stable serialization for the same state.
        write_clarification_state(tmp_path, _state())
        first = clarification_state_path(tmp_path).read_text()
        write_clarification_state(tmp_path, _state())
        second = clarification_state_path(tmp_path).read_text()
        assert first == second
