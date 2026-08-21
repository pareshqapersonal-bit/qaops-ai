"""Phase 41E question-id uniqueness tests.

Proves the 41E-3 merge boundary re-scopes each new question batch so question_ids
are globally unique across rounds (the agent numbers every batch from Q-001, which
collided once batches were merged). Existing ids/ordering/content are preserved.
Uses mocked LLM responses - no live provider calls.
"""

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from qaops.clarification.enums import (
    AnswerType,
    ClarificationStatus,
    QuestionPriority,
    QuestionStatus,
)
from qaops.clarification.models import ClarificationAnswer, ClarificationQuestion
from qaops.clarification.service import ClarificationService, _reindex_new_questions
from qaops.clarification.state_store import load_clarification_state
from qaops.config import QAOpsSettings
from qaops.llm import MockLLMClient


def _q(qid: str, gap: str = "gap", status: QuestionStatus = QuestionStatus.UNANSWERED):
    return ClarificationQuestion(
        question_id=qid,
        question="Q?",
        priority=QuestionPriority.BLOCKING,
        answer_type=AnswerType.BOOLEAN,
        requirement_id="REQ-001",
        gap_reference=gap,
        options=[],
        reason="r",
        evidence=[],
        status=status,
    )


class TestReindexUnit:
    def test_first_batch_unchanged_when_no_existing(self) -> None:
        # With no existing questions, a batch is renumbered from Q-001 as before.
        new = [_q("Q-001"), _q("Q-002")]
        out = _reindex_new_questions([], new)
        assert [q.question_id for q in out] == ["Q-001", "Q-002"]

    def test_second_batch_continues_after_max(self) -> None:
        existing = [_q("Q-001"), _q("Q-002"), _q("Q-003")]
        new = [_q("Q-001", "n1"), _q("Q-002", "n2")]
        out = _reindex_new_questions(existing, new)
        assert [q.question_id for q in out] == ["Q-004", "Q-005"]

    def test_existing_ids_never_change(self) -> None:
        existing = [_q("Q-001"), _q("Q-002")]
        before = [q.question_id for q in existing]
        _reindex_new_questions(existing, [_q("Q-001", "n")])
        assert [q.question_id for q in existing] == before

    def test_content_preserved_only_id_changes(self) -> None:
        existing = [_q("Q-001")]
        new = [_q("Q-001", "important gap", status=QuestionStatus.UNANSWERED)]
        out = _reindex_new_questions(existing, new)
        assert out[0].question_id == "Q-002"
        assert out[0].gap_reference == "important gap"
        assert out[0].priority is QuestionPriority.BLOCKING
        assert out[0].answer_type is AnswerType.BOOLEAN
        assert out[0].requirement_id == "REQ-001"

    def test_ordering_preserved(self) -> None:
        existing = [_q("Q-001")]
        new = [_q("Q-001", "a"), _q("Q-002", "b"), _q("Q-003", "c")]
        out = _reindex_new_questions(existing, new)
        assert [q.gap_reference for q in out] == ["a", "b", "c"]
        assert [q.question_id for q in out] == ["Q-002", "Q-003", "Q-004"]

    def test_non_numeric_existing_ids_handled_safely(self) -> None:
        # A non-Q-### existing id is ignored for max but never collided with.
        existing = [_q("custom-id"), _q("Q-002")]
        out = _reindex_new_questions(existing, [_q("Q-001", "n")])
        assert out[0].question_id == "Q-003"  # after max numeric (2)
        assert existing[0].question_id == "custom-id"  # untouched

    def test_no_duplicates_across_three_rounds(self) -> None:
        r1 = _reindex_new_questions([], [_q("Q-001"), _q("Q-002"), _q("Q-003")])
        r2 = _reindex_new_questions(r1, [_q("Q-001", "b1"), _q("Q-002", "b2")])
        merged = [*r1, *r2]
        r3 = _reindex_new_questions(merged, [_q("Q-001", "c1")])
        all_ids = [q.question_id for q in (*r1, *r2, *r3)]
        assert len(all_ids) == len(set(all_ids))
        assert all_ids == ["Q-001", "Q-002", "Q-003", "Q-004", "Q-005", "Q-006"]


# --- integration through the iterative loop ---------------------------------

_ANALYZER = json.dumps(
    {
        "requirements": [
            {
                "title": "Store availability",
                "description": "User checks store availability.",
                "source_excerpt": "check store availability",
            }
        ]
    }
)
_GAP_BLOCKER = json.dumps(
    {
        "gaps": [
            {
                "description": "Retry undefined",
                "severity": "blocker",
                "requirement_id": "REQ-001",
                "suggested_question": "Retry?",
            }
        ]
    }
)
_GAP_TIMEOUT = json.dumps(
    {
        "gaps": [
            {
                "description": "Timeout undefined",
                "severity": "blocker",
                "requirement_id": "REQ-001",
                "suggested_question": "Timeout?",
            }
        ]
    }
)
_GAP_NONE = json.dumps({"gaps": []})
_AGENT = json.dumps(
    {
        "questions": [
            {
                "gap_index": 0,
                "skip": False,
                "question": "Q?",
                "answer_type": "boolean",
                "options": [],
                "reason": "r",
            }
        ]
    }
)


def _patch(client: object):
    from qaops.execution.registry import get_provider

    return (
        patch("qaops.execution.resilient_call.create_client", return_value=client),
        patch(
            "qaops.execution.resilient_call.fallback_providers",
            return_value=[get_provider("nvidia")],
        ),
    )


@contextmanager
def _clarify(client: object):
    p1, p2 = _patch(client)
    with p1, p2:
        yield


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "run"
    (ws / "input").mkdir(parents=True)
    (ws / "input" / "t.md").write_text("User checks store availability.", encoding="utf-8")
    return ws


def _started(tmp_path: Path):
    ws = _ws(tmp_path)
    svc = ClarificationService(QAOpsSettings(output_dir=ws / "output"))
    with _clarify(MockLLMClient([_ANALYZER, _GAP_BLOCKER, _AGENT])):
        state = svc.start("run_1", ws / "input" / "t.md", ws)
    return svc, ws, state


class TestReindexIntegration:
    def test_second_round_ids_unique_and_persisted(self, tmp_path: Path) -> None:
        svc, ws, state = _started(tmp_path)
        assert [q.question_id for q in state.questions] == ["Q-001"]
        ans = [
            ClarificationAnswer(question_id="Q-001", answer_type=AnswerType.BOOLEAN, answer="true")
        ]
        # Answer -> re-run surfaces a NEW gap -> new batch, which the agent numbers
        # Q-001 again; the merge must re-scope it to Q-002.
        with _clarify(MockLLMClient([_GAP_TIMEOUT, _AGENT])):
            new = svc.submit_answers(ws, ans)
        ids = [q.question_id for q in new.questions]
        assert ids == ["Q-001", "Q-002"]  # unique across rounds
        assert len(ids) == len(set(ids))
        # Persisted state carries the unique ids on reload.
        reloaded = load_clarification_state(ws)
        assert [q.question_id for q in reloaded.questions] == ["Q-001", "Q-002"]

    def test_answered_question_retains_id_and_status(self, tmp_path: Path) -> None:
        svc, ws, state = _started(tmp_path)
        ans = [
            ClarificationAnswer(question_id="Q-001", answer_type=AnswerType.BOOLEAN, answer="true")
        ]
        with _clarify(MockLLMClient([_GAP_TIMEOUT, _AGENT])):
            new = svc.submit_answers(ws, ans)
        q1 = next(q for q in new.questions if q.question_id == "Q-001")
        assert q1.status is QuestionStatus.ANSWERED  # retained id + answered status

    def test_answers_for_two_rounds_do_not_collide(self, tmp_path: Path) -> None:
        # After two rounds with unique ids, answers for Q-001 and Q-002 coexist.
        svc, ws, state = _started(tmp_path)
        with _clarify(MockLLMClient([_GAP_TIMEOUT, _AGENT])):
            r1 = svc.submit_answers(
                ws,
                [
                    ClarificationAnswer(
                        question_id="Q-001",
                        answer_type=AnswerType.BOOLEAN,
                        answer="true",
                    )
                ],
            )
        assert [q.question_id for q in r1.questions] == ["Q-001", "Q-002"]
        # Answer the new Q-002 -> no gaps -> ready. Both answers persist distinctly.
        with _clarify(MockLLMClient([_GAP_NONE])):
            r2 = svc.submit_answers(
                ws,
                [
                    ClarificationAnswer(
                        question_id="Q-002",
                        answer_type=AnswerType.BOOLEAN,
                        answer="false",
                    )
                ],
            )
        answered_ids = {a.question_id for a in r2.answers}
        assert answered_ids == {"Q-001", "Q-002"}  # no collision
        assert len(r2.answers) == 2

    def test_single_round_behavior_unchanged(self, tmp_path: Path) -> None:
        # A run that reaches ready in one answer round (no new gaps) keeps Q-001.
        svc, ws, state = _started(tmp_path)
        with _clarify(MockLLMClient([_GAP_NONE])):
            new = svc.submit_answers(
                ws,
                [
                    ClarificationAnswer(
                        question_id="Q-001",
                        answer_type=AnswerType.BOOLEAN,
                        answer="true",
                    )
                ],
            )
        assert [q.question_id for q in new.questions] == ["Q-001"]
        assert new.status is ClarificationStatus.READY_FOR_TEST_DESIGN

    def test_retry_after_failure_no_duplicate_ids(self, tmp_path: Path) -> None:
        # Persist-first: a failed round persists answers; a retry must not create
        # duplicate ids.
        svc, ws, state = _started(tmp_path)
        ans = [
            ClarificationAnswer(question_id="Q-001", answer_type=AnswerType.BOOLEAN, answer="true")
        ]

        def boom(*_a, **_k):
            raise RuntimeError("fail once")

        import pytest

        with (
            patch.object(svc, "_rerun_gap_analysis", side_effect=boom),
            pytest.raises(RuntimeError),
        ):
            svc.submit_answers(ws, ans)
        # Retry succeeds with a new gap; ids stay unique.
        with _clarify(MockLLMClient([_GAP_TIMEOUT, _AGENT])):
            new = svc.submit_answers(
                ws,
                [
                    ClarificationAnswer(
                        question_id="Q-001",
                        answer_type=AnswerType.BOOLEAN,
                        answer="true",
                    )
                ],
            )
        ids = [q.question_id for q in new.questions]
        assert len(ids) == len(set(ids))
        assert ids == ["Q-001", "Q-002"]
