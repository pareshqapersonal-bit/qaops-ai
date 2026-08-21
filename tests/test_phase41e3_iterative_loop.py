"""Phase 41E-3 tests: the iterative clarification loop in submit_answers.

Covers the answer -> augment -> re-run gaps -> diff -> new-batch loop: new gaps
generate a new round (RE_ANALYZING), already-asked gaps are never re-asked,
resolved gaps let the run reach READY, proceed-with-assumptions records traceable
assumptions, the round cap forces proceed, and asked_gap_signatures accumulates.
The one-shot clarify=false flow is not exercised here (it never calls this path).

The clarification LLM seam is patched with a MockLLMClient whose scripted responses
are consumed in call order: for a submit_answers round that discovers new gaps the
order is [gap_report, question_batch]; for one that discovers none it is just
[gap_report].
"""

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from qaops.clarification.enums import (
    AnswerType,
    AssumptionSource,
    ClarificationStatus,
    QuestionStatus,
)
from qaops.clarification.models import ClarificationAnswer
from qaops.clarification.service import ClarificationService
from qaops.clarification.state_store import load_clarification_state
from qaops.config import QAOpsSettings
from qaops.llm import MockLLMClient

# --- scripted LLM responses -------------------------------------------------

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


def _gap(description: str, requirement_id: str | None = "REQ-001") -> dict:
    return {
        "description": description,
        "severity": "blocker",
        "requirement_id": requirement_id,
        "suggested_question": f"Clarify: {description}?",
    }


def _gap_report(*descriptions: str) -> str:
    return json.dumps({"gaps": [_gap(d) for d in descriptions]})


_GAP_NONE = json.dumps({"gaps": []})

# One blocking question the agent returns for a single-gap batch (gap_index 0).
_AGENT_ONE = json.dumps(
    {
        "questions": [
            {
                "gap_index": 0,
                "skip": False,
                "question": "What is the retry policy?",
                "answer_type": "boolean",
                "options": [],
                "reason": "Needed to design tests.",
            }
        ]
    }
)

# A second-round agent batch for a newly discovered gap (again gap_index 0 of the
# NEW-gaps-only batch passed to generate_questions).
_AGENT_SECOND = json.dumps(
    {
        "questions": [
            {
                "gap_index": 0,
                "skip": False,
                "question": "What is the timeout?",
                "answer_type": "boolean",
                "options": [],
                "reason": "Needed to design tests.",
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


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "run"
    (ws / "input").mkdir(parents=True)
    (ws / "input" / "prd.md").write_text("User checks store availability.", encoding="utf-8")
    return ws


def _start(tmp_path: Path):
    """Start a clarification with one blocking question (gap: retry policy)."""
    ws = _workspace(tmp_path)
    svc = ClarificationService(QAOpsSettings(output_dir=ws / "output"))
    start_client = MockLLMClient([_ANALYZER, _gap_report("retry policy undefined"), _AGENT_ONE])
    with _clarify(start_client):
        state = svc.start("run_1", ws / "input" / "prd.md", ws)
    return svc, ws, state


def _answer_first(state) -> list[ClarificationAnswer]:
    return [
        ClarificationAnswer(
            question_id=state.questions[0].question_id,
            answer_type=AnswerType.BOOLEAN,
            answer="true",
        )
    ]


class TestIterativeLoop:
    def test_answer_resolves_gap_reaches_ready(self, tmp_path: Path) -> None:
        # Answer the blocker; the re-run finds no gaps -> ready, no new questions.
        svc, ws, state = _start(tmp_path)
        with _clarify(MockLLMClient([_GAP_NONE])):
            new = svc.submit_answers(ws, _answer_first(state))
        assert new.readiness.ready is True
        assert new.status is ClarificationStatus.READY_FOR_TEST_DESIGN
        assert len(new.questions) == 1  # no new batch appended

    def test_new_gap_generates_new_round(self, tmp_path: Path) -> None:
        # Answer the blocker; the re-run surfaces a NEW gap -> a new question batch
        # and RE_ANALYZING status, iteration advanced.
        svc, ws, state = _start(tmp_path)
        with _clarify(MockLLMClient([_gap_report("timeout undefined"), _AGENT_SECOND])):
            new = svc.submit_answers(ws, _answer_first(state))
        assert new.status is ClarificationStatus.RE_ANALYZING
        assert new.readiness.ready is False
        assert len(new.questions) == 2  # original answered + new one
        assert new.questions[1].gap_reference == "timeout undefined"
        assert new.iteration == 2

    def test_already_asked_gap_not_reasked(self, tmp_path: Path) -> None:
        # The re-run returns the SAME gap that was already asked -> PERSISTING, so no
        # duplicate question is generated (still only the original question).
        svc, ws, state = _start(tmp_path)
        with _clarify(MockLLMClient([_gap_report("retry policy undefined")])):
            new = svc.submit_answers(ws, _answer_first(state))
        assert len(new.questions) == 1  # no duplicate appended
        # The blocker was answered, and no new gap -> ready.
        assert new.readiness.ready is True

    def test_asked_signatures_accumulate(self, tmp_path: Path) -> None:
        svc, ws, state = _start(tmp_path)
        # Round 1 seeds one signature; a new gap adds a second.
        with _clarify(MockLLMClient([_gap_report("timeout undefined"), _AGENT_SECOND])):
            new = svc.submit_answers(ws, _answer_first(state))
        assert len(new.asked_gap_signatures) == 2

    def test_multi_round_new_then_resolved_reaches_ready(self, tmp_path: Path) -> None:
        # Round 1: answer -> new gap -> new batch (RE_ANALYZING).
        svc, ws, state = _start(tmp_path)
        with _clarify(MockLLMClient([_gap_report("timeout undefined"), _AGENT_SECOND])):
            r1 = svc.submit_answers(ws, _answer_first(state))
        assert r1.status is ClarificationStatus.RE_ANALYZING
        # Round 2: answer the new question -> re-run finds nothing -> READY.
        ans2 = [
            ClarificationAnswer(
                question_id=r1.questions[1].question_id,
                answer_type=AnswerType.BOOLEAN,
                answer="true",
            )
        ]
        with _clarify(MockLLMClient([_GAP_NONE])):
            r2 = svc.submit_answers(ws, ans2)
        assert r2.readiness.ready is True
        assert r2.status is ClarificationStatus.READY_FOR_TEST_DESIGN


class TestProceedWithAssumptions:
    def test_proceed_records_traceable_assumptions(self, tmp_path: Path) -> None:
        # Proceeding with an unanswered blocker records an assumption and goes ready,
        # WITHOUT re-running gap analysis (no LLM needed on this path).
        svc, ws, state = _start(tmp_path)
        new = svc.submit_answers(ws, [], proceed_with_assumptions=True)
        assert new.readiness.ready is True
        assert new.status is ClarificationStatus.READY_FOR_TEST_DESIGN
        assert new.questions[0].status is QuestionStatus.SKIPPED
        assert len(new.assumptions) == 1
        # The assumption is traceable back to its requirement/question.
        assumption = new.assumptions[0]
        assert assumption.requirement_id == "REQ-001"
        assert assumption.question_id == new.questions[0].question_id
        assert assumption.source in {
            AssumptionSource.USER_SKIP,
            AssumptionSource.USER_PROCEED_UNRESOLVED,
        }

    def test_proceed_persists(self, tmp_path: Path) -> None:
        svc, ws, state = _start(tmp_path)
        svc.submit_answers(ws, [], proceed_with_assumptions=True)
        reloaded = load_clarification_state(ws)
        assert reloaded is not None
        assert len(reloaded.assumptions) == 1
        assert reloaded.readiness.ready is True


class TestRoundCap:
    def test_round_cap_forces_proceed(self, tmp_path: Path) -> None:
        from qaops.clarification.service import (
            MAX_CLARIFICATION_ROUNDS,
            ClarificationRoundLimitError,
        )
        from qaops.clarification.state_store import write_clarification_state

        svc, ws, state = _start(tmp_path)
        capped = state.model_copy(update={"iteration": MAX_CLARIFICATION_ROUNDS})
        write_clarification_state(ws, capped)
        # Not proceeding, blocker still open, re-run keeps it unresolved -> the cap is
        # enforced (persisting gap, no progress).
        import pytest

        with (
            pytest.raises(ClarificationRoundLimitError),
            _clarify(MockLLMClient([_gap_report("retry policy undefined")])),
        ):
            svc.submit_answers(ws, [])

    def test_proceed_bypasses_cap(self, tmp_path: Path) -> None:
        from qaops.clarification.service import MAX_CLARIFICATION_ROUNDS
        from qaops.clarification.state_store import write_clarification_state

        svc, ws, state = _start(tmp_path)
        capped = state.model_copy(update={"iteration": MAX_CLARIFICATION_ROUNDS})
        write_clarification_state(ws, capped)
        # Proceeding is always allowed, even at the cap (no exception, goes ready).
        new = svc.submit_answers(ws, [], proceed_with_assumptions=True)
        assert new.readiness.ready is True
