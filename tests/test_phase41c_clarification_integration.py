"""Phase 41C-1 tests: clarification service + API integration.

Covers the bounded-analysis service (compose existing analyzer+gap, generate
questions, persist 41A state, apply answers, round cap) and the two API endpoints
(GET clarifications, POST answers), plus the critical guarantee that clarify=false /
omitted leaves the one-shot flow unchanged. LLM is mocked; no live calls.
"""

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from qaops.clarification.enums import AnswerType, ClarificationStatus, QuestionStatus
from qaops.clarification.models import ClarificationAnswer
from qaops.clarification.service import (
    MAX_CLARIFICATION_ROUNDS,
    ClarificationNotFoundError,
    ClarificationRoundLimitError,
    ClarificationService,
)
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
                "question": "Should the user be allowed to retry when the API fails?",
                "answer_type": "boolean",
                "options": [],
                "reason": "error-path coverage",
            }
        ]
    }
)


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "run_1"
    (ws / "input").mkdir(parents=True)
    (ws / "output").mkdir(parents=True)
    (ws / "input" / "ticket.md").write_text("User checks store availability by pincode.")
    return ws


def _service(tmp_path: Path, responses: list[str]) -> tuple[ClarificationService, Path]:
    ws = _workspace(tmp_path)
    settings = QAOpsSettings(output_dir=ws / "output")
    svc = ClarificationService(settings)
    client = MockLLMClient(responses)
    return svc, ws, client  # type: ignore[return-value]


def _patch_clarification_llm(client: object):
    """Patch the resilient-call seam so clarification LLM calls use `client`.

    Phase 41C-4 moved client construction into resilient_call; the candidate chain
    comes from fallback_providers (key-gated to nvidia in tests). Patching both
    reproduces the old single-provider behaviour: one nvidia candidate, one attempt,
    served by the mock client.
    """
    from qaops.execution.registry import get_provider

    return (
        patch("qaops.execution.resilient_call.create_client", return_value=client),
        patch(
            "qaops.execution.resilient_call.fallback_providers",
            return_value=[get_provider("nvidia")],
        ),
    )


@contextmanager
def _clarify_llm(client: object):
    p1, p2 = _patch_clarification_llm(client)
    with p1, p2:
        yield


class TestServiceStart:
    def test_start_generates_questions_and_persists(self, tmp_path: Path) -> None:
        svc, ws, client = _service(tmp_path, [_ANALYZER, _GAP_BLOCKER, _AGENT_ONE])
        with _clarify_llm(client):
            state = svc.start("run_1", ws / "input" / "ticket.md", ws)
        assert len(state.questions) == 1
        assert state.status is ClarificationStatus.CLARIFYING
        assert state.readiness.ready is False
        assert load_clarification_state(ws) == state

    def test_start_ready_when_no_blocking_gaps(self, tmp_path: Path) -> None:
        svc, ws, client = _service(tmp_path, [_ANALYZER, _GAP_NONE])
        with _clarify_llm(client):
            state = svc.start("run_1", ws / "input" / "ticket.md", ws)
        assert state.questions == []
        assert state.status is ClarificationStatus.READY_FOR_TEST_DESIGN
        assert state.readiness.ready is True

    def test_start_uses_single_client(self, tmp_path: Path) -> None:
        # analyzer + gap + agent = exactly 3 LLM calls on one shared client.
        svc, ws, client = _service(tmp_path, [_ANALYZER, _GAP_BLOCKER, _AGENT_ONE])
        with _clarify_llm(client):
            svc.start("run_1", ws / "input" / "ticket.md", ws)
        assert client.call_count == 3


class TestServiceAnswers:
    def _started(self, tmp_path: Path):
        svc, ws, client = _service(tmp_path, [_ANALYZER, _GAP_BLOCKER, _AGENT_ONE])
        with _clarify_llm(client):
            state = svc.start("run_1", ws / "input" / "ticket.md", ws)
        return svc, ws, state

    def test_answer_makes_ready(self, tmp_path: Path) -> None:
        svc, ws, state = self._started(tmp_path)
        ans = [
            ClarificationAnswer(
                question_id=state.questions[0].question_id,
                answer_type=AnswerType.BOOLEAN,
                answer="true",
            )
        ]
        # The answer round now re-runs gap analysis (41E-3); supply an empty-gaps
        # response so no new questions are generated and the run becomes ready.
        with _clarify_llm(MockLLMClient([_GAP_NONE])):
            new_state = svc.submit_answers(ws, ans)
        assert new_state.readiness.ready is True
        assert new_state.status is ClarificationStatus.READY_FOR_TEST_DESIGN
        assert new_state.questions[0].status is QuestionStatus.ANSWERED

    def test_answers_persisted(self, tmp_path: Path) -> None:
        svc, ws, state = self._started(tmp_path)
        ans = [
            ClarificationAnswer(
                question_id=state.questions[0].question_id,
                answer_type=AnswerType.BOOLEAN,
                answer="yes",
            )
        ]
        with _clarify_llm(MockLLMClient([_GAP_NONE])):
            svc.submit_answers(ws, ans)
        reloaded = load_clarification_state(ws)
        assert len(reloaded.answers) == 1

    def test_proceed_with_assumptions_marks_skipped(self, tmp_path: Path) -> None:
        svc, ws, state = self._started(tmp_path)
        new_state = svc.submit_answers(ws, [], proceed_with_assumptions=True)
        assert new_state.questions[0].status is QuestionStatus.SKIPPED
        assert len(new_state.assumptions) == 1
        assert new_state.readiness.ready is True

    def test_contradictory_answers_rejected(self, tmp_path: Path) -> None:
        svc, ws, state = self._started(tmp_path)
        qid = state.questions[0].question_id
        ans = [
            ClarificationAnswer(question_id=qid, answer_type=AnswerType.BOOLEAN, answer="true"),
            ClarificationAnswer(question_id=qid, answer_type=AnswerType.BOOLEAN, answer="false"),
        ]
        with pytest.raises(ValueError, match="Contradictory"):
            svc.submit_answers(ws, ans)

    def test_no_state_raises(self, tmp_path: Path) -> None:
        ws = _workspace(tmp_path)
        svc = ClarificationService(QAOpsSettings(output_dir=ws / "output"))
        with pytest.raises(ClarificationNotFoundError):
            svc.submit_answers(ws, [])

    def test_round_cap_enforced(self, tmp_path: Path) -> None:
        svc, ws, state = self._started(tmp_path)
        # Force iteration to the cap with the blocker still unanswered.
        from qaops.clarification.state_store import write_clarification_state

        capped = state.model_copy(update={"iteration": MAX_CLARIFICATION_ROUNDS})
        write_clarification_state(ws, capped)
        # The re-run finds the blocker still open (a persisting gap), readiness stays
        # false, and the cap is enforced. Supply the gap re-run response.
        with (
            pytest.raises(ClarificationRoundLimitError),
            _clarify_llm(MockLLMClient([_GAP_BLOCKER])),
        ):
            svc.submit_answers(ws, [])  # unanswered blocker, not proceeding


class TestApiEndpoints:
    def test_get_clarifications_404_unknown_run(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from qaops.api.app import APIConfig, create_app

        app = create_app(APIConfig(runtime_dir=tmp_path / "runs"))
        with TestClient(app) as client:
            r = client.get("/api/v1/runs/does_not_exist/clarifications")
        assert r.status_code == 404

    def test_get_clarifications_409_for_oneshot_run(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from qaops.api.app import APIConfig, create_app

        cfg = APIConfig(runtime_dir=tmp_path / "runs")
        app = create_app(cfg)
        # Create a run through the app's own store via a one-shot submit (patched so
        # no real pipeline runs), then query its clarifications -> 409 (no state).
        with (
            patch("qaops.api.app.execute_run", side_effect=lambda *a, **k: None),
            TestClient(app) as client,
        ):
            created = client.post(
                "/api/v1/design",
                files={"file": ("req.md", b"A user logs in.", "text/markdown")},
            )
            run_id = created.json()["run_id"]
            r = client.get(f"/api/v1/runs/{run_id}/clarifications")
        assert r.status_code == 409


class TestOneShotUnchanged:
    def test_clarify_omitted_uses_execute_run(self, tmp_path: Path) -> None:
        # The default (no clarify flag) must schedule the existing one-shot task,
        # never the clarification task.
        from fastapi.testclient import TestClient

        from qaops.api.app import APIConfig, create_app

        cfg = APIConfig(runtime_dir=tmp_path / "runs")
        app = create_app(cfg)
        scheduled: list = []

        # Patch both task entry points to observe which is scheduled.
        with (
            patch(
                "qaops.api.app.execute_run", side_effect=lambda *a, **k: scheduled.append("oneshot")
            ),
            patch(
                "qaops.api.app.execute_clarification_analysis",
                side_effect=lambda *a, **k: scheduled.append("clarify"),
            ),
            TestClient(app) as client,
        ):
            r = client.post(
                "/api/v1/design",
                files={"file": ("req.md", b"The system shall let a user log in.", "text/markdown")},
            )
        assert r.status_code == 202
        assert scheduled == ["oneshot"]  # one-shot path, not clarification

    def test_clarify_true_uses_clarification_task(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from qaops.api.app import APIConfig, create_app

        cfg = APIConfig(runtime_dir=tmp_path / "runs")
        app = create_app(cfg)
        scheduled: list = []

        with (
            patch(
                "qaops.api.app.execute_run", side_effect=lambda *a, **k: scheduled.append("oneshot")
            ),
            patch(
                "qaops.api.app.execute_clarification_analysis",
                side_effect=lambda *a, **k: scheduled.append("clarify"),
            ),
            TestClient(app) as client,
        ):
            r = client.post(
                "/api/v1/design",
                files={"file": ("req.md", b"The system shall let a user log in.", "text/markdown")},
                data={"clarify": "true"},
            )
        assert r.status_code == 202
        assert scheduled == ["clarify"]  # clarification path


class TestTicketClarifyParity:
    """Phase 41D: the ticket submit endpoint honors clarify=true (backend parity)."""

    def _app(self, tmp_path: Path):
        from qaops.api.app import APIConfig, create_app

        return create_app(APIConfig(runtime_dir=tmp_path / "runs"))

    def test_ticket_clarify_true_schedules_clarification(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app = self._app(tmp_path)
        scheduled: list = []
        with (
            patch(
                "qaops.api.app.execute_run", side_effect=lambda *a, **k: scheduled.append("oneshot")
            ),
            patch(
                "qaops.api.app.execute_clarification_analysis",
                side_effect=lambda *a, **k: scheduled.append("clarify"),
            ),
            TestClient(app) as client,
        ):
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "T", "description": "A login flow.", "clarify": "true"},
            )
        assert r.status_code == 202
        assert scheduled == ["clarify"]

    def test_ticket_clarify_omitted_uses_oneshot(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        app = self._app(tmp_path)
        scheduled: list = []
        with (
            patch(
                "qaops.api.app.execute_run", side_effect=lambda *a, **k: scheduled.append("oneshot")
            ),
            patch(
                "qaops.api.app.execute_clarification_analysis",
                side_effect=lambda *a, **k: scheduled.append("clarify"),
            ),
            TestClient(app) as client,
        ):
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "T", "description": "A login flow."},
            )
        assert r.status_code == 202
        assert scheduled == ["oneshot"]
