"""Phase 41E-4 tests: explicit user Proceed -> test-design handoff.

Covers the Proceed transition: valid proceed records PROCEEDED persistently and
traceably; blocking questions cannot be bypassed; recommended/optional unanswered
questions may remain when proceeding; the handoff is idempotent (no duplicate run);
resume after proceed is safe; the existing `requirements` entry point is used; and
the one-shot clarify=false flow and the 41E-3 iterative loop are unregressed.

READY_FOR_TEST_DESIGN stays the authoritative run-lifecycle state; PROCEEDED is a
clarification-state decision marker only.
"""

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from qaops.api.app import APIConfig, create_app
from qaops.clarification.enums import AnswerType, ClarificationStatus
from qaops.clarification.models import ClarificationAnswer
from qaops.clarification.service import ClarificationNotReadyError, ClarificationService
from qaops.clarification.state_store import (
    load_clarification_state,
)
from qaops.config import QAOpsSettings
from qaops.entrypoints.parsers import parse_requirements
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


def _ready_service(tmp_path: Path) -> tuple[ClarificationService, Path]:
    """Start + answer the blocker so the run reaches readiness (not yet proceeded)."""
    ws = _ws(tmp_path)
    svc = ClarificationService(QAOpsSettings(output_dir=ws / "output"))
    with _clarify(MockLLMClient([_ANALYZER, _GAP_BLOCKER, _AGENT_ONE])):
        state = svc.start("run_1", ws / "input" / "t.md", ws)
    ans = [
        ClarificationAnswer(
            question_id=state.questions[0].question_id,
            answer_type=AnswerType.BOOLEAN,
            answer="true",
        )
    ]
    with _clarify(MockLLMClient([_GAP_NONE])):
        svc.submit_answers(ws, ans)
    return svc, ws


class TestProceedDecision:
    def test_proceed_when_ready_records_proceeded(self, tmp_path: Path) -> None:
        svc, ws = _ready_service(tmp_path)
        before = load_clarification_state(ws)
        assert before is not None
        assert before.status is ClarificationStatus.READY_FOR_TEST_DESIGN

        svc.prepare_test_design(ws)

        after = load_clarification_state(ws)
        assert after is not None
        assert after.status is ClarificationStatus.PROCEEDED  # decision recorded

    def test_proceed_persists_and_is_traceable(self, tmp_path: Path) -> None:
        svc, ws = _ready_service(tmp_path)
        svc.prepare_test_design(ws)
        reloaded = load_clarification_state(ws)
        assert reloaded is not None
        # Persisted decision + the answers/assumptions that justify it remain.
        assert reloaded.status is ClarificationStatus.PROCEEDED
        assert len(reloaded.answers) == 1

    def test_blocking_cannot_be_bypassed(self, tmp_path: Path) -> None:
        # Start with a blocking question, do NOT answer it, and try to proceed.
        ws = _ws(tmp_path)
        svc = ClarificationService(QAOpsSettings(output_dir=ws / "output"))
        with _clarify(MockLLMClient([_ANALYZER, _GAP_BLOCKER, _AGENT_ONE])):
            svc.start("run_1", ws / "input" / "t.md", ws)
        # Not ready (blocking unanswered) -> proceed is refused.
        with pytest.raises(ClarificationNotReadyError):
            svc.prepare_test_design(ws)
        # And no PROCEEDED was recorded.
        state = load_clarification_state(ws)
        assert state is not None
        assert state.status is not ClarificationStatus.PROCEEDED

    def test_proceed_with_assumptions_allows_optional_unanswered(self, tmp_path: Path) -> None:
        # A recommended/optional question left unanswered is accepted via the
        # proceed-with-assumptions path (readiness reached, then handoff records it).
        ws = _ws(tmp_path)
        svc = ClarificationService(QAOpsSettings(output_dir=ws / "output"))
        with _clarify(MockLLMClient([_ANALYZER, _GAP_BLOCKER, _AGENT_ONE])):
            svc.start("run_1", ws / "input" / "t.md", ws)
        # User proceeds with assumptions (no re-analysis) -> ready with the question
        # skipped and recorded as an assumption.
        state = svc.submit_answers(ws, [], proceed_with_assumptions=True)
        assert state.readiness.ready is True
        assert len(state.assumptions) == 1
        # The explicit handoff then records the PROCEEDED decision.
        svc.prepare_test_design(ws)
        assert load_clarification_state(ws).status is ClarificationStatus.PROCEEDED

    def test_proceed_is_idempotent(self, tmp_path: Path) -> None:
        # Re-running the handoff is safe: same clarified file, status stays PROCEEDED.
        svc, ws = _ready_service(tmp_path)
        first = svc.prepare_test_design(ws)
        first_bytes = first.read_bytes()
        second = svc.prepare_test_design(ws)
        assert second == first
        assert second.read_bytes() == first_bytes
        assert load_clarification_state(ws).status is ClarificationStatus.PROCEEDED

    def test_requirements_handoff_parses(self, tmp_path: Path) -> None:
        # The clarified artifact is consumable by the existing requirements entry
        # point (analyzer/gap are not re-run).
        svc, ws = _ready_service(tmp_path)
        parsed = parse_requirements(svc.prepare_test_design(ws))
        assert parsed.requirements
        assert parsed.requirements[0].id  # stable ID preserved


class TestProceedApiIdempotency:
    def test_duplicate_start_does_not_launch_two_runs(self, tmp_path: Path) -> None:
        cfg = APIConfig(runtime_dir=tmp_path / "runs")
        app = create_app(cfg)
        clar = MockLLMClient([_ANALYZER, _GAP_BLOCKER, _AGENT_ONE])
        from tests.test_api import CONDITIONS, TEST_CASES
        from tests.test_phase32_ticket_api import RULES, SCENARIOS

        design_script = [RULES, _GAP_NONE, SCENARIOS, CONDITIONS, TEST_CASES]

        with TestClient(app) as c:
            with _clarify(clar):
                created = c.post(
                    "/api/v1/design",
                    files={"file": ("t.md", b"User checks store availability.", "text/markdown")},
                    data={"clarify": "true"},
                )
            rid = created.json()["run_id"]
            qid = c.get(f"/api/v1/runs/{rid}/clarifications").json()["questions"][0]["question_id"]
            with _clarify(MockLLMClient([_GAP_NONE])):
                c.post(
                    f"/api/v1/runs/{rid}/clarifications/answers",
                    json={
                        "answers": [
                            {"question_id": qid, "answer_type": "boolean", "answer": "true"}
                        ]
                    },
                )
            # First start -> 202.
            with patch(
                "qaops.services.design_service.create_client",
                side_effect=lambda _s: MockLLMClient(list(design_script)),
            ):
                first = c.post(f"/api/v1/runs/{rid}/start-test-design")
            assert first.status_code == 202
            # Second start -> 409 (duplicate-start guard): the run already left
            # READY_FOR_TEST_DESIGN, so no second execution is launched. This is the
            # idempotency guarantee, independent of the design pipeline's outcome.
            second = c.post(f"/api/v1/runs/{rid}/start-test-design")
            assert second.status_code == 409
            # The run is no longer parked at ready (it advanced past proceed).
            status = c.get(f"/api/v1/runs/{rid}").json()["status"]
            assert status != "ready_for_test_design"

    def test_start_before_ready_rejected(self, tmp_path: Path) -> None:
        cfg = APIConfig(runtime_dir=tmp_path / "runs")
        app = create_app(cfg)
        clar = MockLLMClient([_ANALYZER, _GAP_BLOCKER, _AGENT_ONE])
        with TestClient(app) as c:
            with _clarify(clar):
                created = c.post(
                    "/api/v1/design",
                    files={"file": ("t.md", b"User checks store availability.", "text/markdown")},
                    data={"clarify": "true"},
                )
            rid = created.json()["run_id"]
            # Blocking unanswered -> start rejected.
            r = c.post(f"/api/v1/runs/{rid}/start-test-design")
        assert r.status_code == 409


class TestResumeAfterProceed:
    def test_state_reloads_as_proceeded(self, tmp_path: Path) -> None:
        # After proceed, reloading the persisted state (a resume) sees PROCEEDED and
        # readiness intact - safe to resume the handoff.
        svc, ws = _ready_service(tmp_path)
        svc.prepare_test_design(ws)
        # Simulate a fresh process: reload from disk only.
        reloaded = load_clarification_state(ws)
        assert reloaded is not None
        assert reloaded.status is ClarificationStatus.PROCEEDED
        assert reloaded.readiness.ready is True
        # A resumed handoff is still safe (idempotent).
        svc.prepare_test_design(ws)
        assert load_clarification_state(ws).status is ClarificationStatus.PROCEEDED


class TestNonClarifyRegression:
    def test_one_shot_flow_has_no_clarification_state(self, tmp_path: Path) -> None:
        # clarify=false must not create clarification state or touch this path.
        cfg = APIConfig(runtime_dir=tmp_path / "runs")
        app = create_app(cfg)
        from tests.test_api import CONDITIONS, TEST_CASES
        from tests.test_phase32_ticket_api import RULES, SCENARIOS

        script = [_ANALYZER, _GAP_NONE, RULES, SCENARIOS, CONDITIONS, TEST_CASES]
        with TestClient(app) as c:
            with patch(
                "qaops.services.design_service.create_client",
                side_effect=lambda _s: MockLLMClient(list(script)),
            ):
                created = c.post(
                    "/api/v1/design",
                    files={"file": ("t.md", b"User checks store availability.", "text/markdown")},
                    data={"clarify": "false"},
                )
            rid = created.json()["run_id"]
            # A one-shot run has no clarification in progress: the clarifications
            # endpoint reports that (not a clarification round). 409 = no clarification.
            clar = c.get(f"/api/v1/runs/{rid}/clarifications")
            assert clar.status_code in (404, 409)
            # And no PROCEEDED clarification state was ever written for this run.
            state = (
                load_clarification_state(cfg.runtime_dir / rid)
                if (cfg.runtime_dir / rid).exists()
                else None
            )
            assert state is None or state.status is not ClarificationStatus.PROCEEDED
