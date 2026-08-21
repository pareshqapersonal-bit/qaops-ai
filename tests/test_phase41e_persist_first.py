"""Phase 41E persist-first durability tests.

Proves that submit_answers persists the user's answers BEFORE the 41E-3 LLM work
(gap re-run + question generation), so a slow/failed LLM round never loses answers
and the run stays safely retryable. Uses mocked LLM clients and injected failures -
no live provider calls.
"""

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from qaops.clarification.enums import AnswerType, ClarificationStatus, QuestionStatus
from qaops.clarification.models import ClarificationAnswer
from qaops.clarification.service import ClarificationService
from qaops.clarification.state_store import load_clarification_state
from qaops.config import QAOpsSettings
from qaops.llm import MockLLMClient

_ANALYZER = json.dumps(
    {
        "requirements": [
            {
                "title": "Store availability",
                "description": "User checks store availability by pincode.",
                "source_excerpt": "check store availability",
            }
        ]
    }
)
_GAP_BLOCKER = json.dumps(
    {
        "gaps": [
            {
                "description": "Retry on API failure undefined",
                "severity": "blocker",
                "requirement_id": "REQ-001",
                "suggested_question": "Retry?",
            }
        ]
    }
)
_GAP_NONE = json.dumps({"gaps": []})
_AGENT_ONE = json.dumps(
    {
        "questions": [
            {
                "gap_index": 0,
                "skip": False,
                "question": "Should the user be allowed to retry?",
                "answer_type": "boolean",
                "options": [],
                "reason": "error-path coverage",
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
    with _clarify(MockLLMClient([_ANALYZER, _GAP_BLOCKER, _AGENT_ONE])):
        state = svc.start("run_1", ws / "input" / "t.md", ws)
    return svc, ws, state


def _answer(state) -> list[ClarificationAnswer]:
    return [
        ClarificationAnswer(
            question_id=state.questions[0].question_id,
            answer_type=AnswerType.BOOLEAN,
            answer="true",
        )
    ]


class TestPersistFirst:
    def test_answers_persisted_before_gap_analysis(self, tmp_path: Path) -> None:
        # Assert the checkpoint is written before the gap re-run by inspecting the
        # persisted state at the moment _rerun_gap_analysis is entered.
        svc, ws, state = _started(tmp_path)
        seen: dict[str, object] = {}
        real = svc._rerun_gap_analysis

        def spy(reqs, source_text, settings):
            persisted = load_clarification_state(ws)
            seen["answers_at_llm_time"] = len(persisted.answers) if persisted else 0
            seen["status_at_llm_time"] = persisted.status if persisted else None
            return real(reqs, source_text, settings)

        with (
            patch.object(svc, "_rerun_gap_analysis", side_effect=spy),
            _clarify(MockLLMClient([_GAP_NONE])),
        ):
            svc.submit_answers(ws, _answer(state))

        # The answer was already durable when gap analysis began.
        assert seen["answers_at_llm_time"] == 1
        assert seen["status_at_llm_time"] is ClarificationStatus.CLARIFYING

    def test_answers_survive_gap_analysis_failure(self, tmp_path: Path) -> None:
        svc, ws, state = _started(tmp_path)

        def boom(*_a, **_k):
            raise RuntimeError("simulated gap-analysis failure")

        with (
            patch.object(svc, "_rerun_gap_analysis", side_effect=boom),
            pytest.raises(RuntimeError),
        ):
            svc.submit_answers(ws, _answer(state))

        # The submitted answer is still persisted and the run is retryable.
        persisted = load_clarification_state(ws)
        assert persisted is not None
        assert len(persisted.answers) == 1
        assert persisted.questions[0].status is QuestionStatus.ANSWERED
        assert persisted.status is ClarificationStatus.CLARIFYING

    def test_answers_survive_question_generation_failure(self, tmp_path: Path) -> None:
        svc, ws, state = _started(tmp_path)

        # Gap re-run succeeds and yields a NEW gap, but question generation fails.
        def new_gap(*_a, **_k):
            from qaops.models.domain import Gap, GapReport
            from qaops.models.enums import GapSeverity

            return GapReport(
                gaps=[
                    Gap(
                        description="timeout undefined",
                        severity=GapSeverity.BLOCKER,
                        requirement_id="REQ-001",
                        suggested_question="Timeout?",
                    )
                ]
            )

        def boom(*_a, **_k):
            raise RuntimeError("simulated question-generation failure")

        with (
            patch.object(svc, "_rerun_gap_analysis", side_effect=new_gap),
            patch.object(svc, "_generate_questions", side_effect=boom),
            pytest.raises(RuntimeError),
        ):
            svc.submit_answers(ws, _answer(state))

        persisted = load_clarification_state(ws)
        assert persisted is not None
        assert len(persisted.answers) == 1
        assert persisted.questions[0].status is QuestionStatus.ANSWERED

    def test_retry_after_failure_does_not_duplicate_answers(self, tmp_path: Path) -> None:
        svc, ws, state = _started(tmp_path)

        def boom(*_a, **_k):
            raise RuntimeError("fail once")

        # First attempt fails after the checkpoint persists the answer.
        with (
            patch.object(svc, "_rerun_gap_analysis", side_effect=boom),
            pytest.raises(RuntimeError),
        ):
            svc.submit_answers(ws, _answer(state))
        assert len(load_clarification_state(ws).answers) == 1

        # Retry with the SAME answer succeeds; answers must not duplicate.
        reloaded = load_clarification_state(ws)
        with _clarify(MockLLMClient([_GAP_NONE])):
            final = svc.submit_answers(ws, _answer(reloaded))
        assert len(final.answers) == 1  # latest-wins merge, no duplicate
        assert final.readiness.ready is True

    def test_success_path_unchanged(self, tmp_path: Path) -> None:
        svc, ws, state = _started(tmp_path)
        with _clarify(MockLLMClient([_GAP_NONE])):
            new = svc.submit_answers(ws, _answer(state))
        assert new.readiness.ready is True
        assert new.status is ClarificationStatus.READY_FOR_TEST_DESIGN
        assert len(new.answers) == 1
        assert new.questions[0].status is QuestionStatus.ANSWERED

    def test_readiness_and_iteration_unchanged_on_new_round(self, tmp_path: Path) -> None:
        # A successful round that surfaces a new gap still advances iteration and
        # stays not-ready, exactly as before the persist-first change.
        svc, ws, state = _started(tmp_path)
        _AGENT_SECOND = json.dumps(
            {
                "questions": [
                    {
                        "gap_index": 0,
                        "skip": False,
                        "question": "What timeout?",
                        "answer_type": "boolean",
                        "options": [],
                        "reason": "coverage",
                    }
                ]
            }
        )
        gap_timeout = json.dumps(
            {
                "gaps": [
                    {
                        "description": "timeout undefined",
                        "severity": "blocker",
                        "requirement_id": "REQ-001",
                        "suggested_question": "Timeout?",
                    }
                ]
            }
        )
        with _clarify(MockLLMClient([gap_timeout, _AGENT_SECOND])):
            new = svc.submit_answers(ws, _answer(state))
        assert new.readiness.ready is False
        assert new.status is ClarificationStatus.RE_ANALYZING
        assert new.iteration == 2
        assert len(new.questions) == 2
