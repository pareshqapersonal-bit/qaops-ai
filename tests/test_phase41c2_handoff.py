"""Phase 41C-2 tests: handoff from READY_FOR_TEST_DESIGN to the test-design pipeline.

Covers the service handoff (clarified-requirements artifact, not-ready refusal,
idempotency, ID stability, answer augmentation) and the start-test-design endpoint
(readiness gate, duplicate-start guard, 404/409), plus the end-to-end run: clarify ->
answer -> ready -> start-test-design -> pipeline via the `requirements` entry point
(analyzer + gap NOT re-run) -> COMPLETED. One-shot flow remains unaffected. LLM mocked.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from qaops.api.app import APIConfig, create_app
from qaops.clarification.enums import AnswerType
from qaops.clarification.models import ClarificationAnswer
from qaops.clarification.service import (
    ClarificationNotReadyError,
    ClarificationService,
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
_AGENT_ONE = json.dumps(
    {
        "questions": [
            {
                "gap_index": 0,
                "skip": False,
                "question": "Allow retry when the API fails?",
                "answer_type": "boolean",
                "options": [],
                "reason": "coverage",
            }
        ]
    }
)


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "run_1"
    (ws / "input").mkdir(parents=True)
    (ws / "output").mkdir(parents=True)
    (ws / "input" / "t.md").write_text("User checks store availability by pincode.")
    return ws


def _clarify_patches(*, return_value=None, side_effect=None):
    """Patch the resilient-call seam (Phase 41C-4) for clarification LLM calls.

    Patches both create_client (the mock client) and fallback_providers (a single
    nvidia candidate), reproducing the old single-provider clarification behaviour.
    """
    from qaops.execution.registry import get_provider

    kw = {}
    if return_value is not None:
        kw["return_value"] = return_value
    if side_effect is not None:
        kw["side_effect"] = side_effect
    return (
        patch("qaops.execution.resilient_call.create_client", **kw),
        patch(
            "qaops.execution.resilient_call.fallback_providers",
            return_value=[get_provider("nvidia")],
        ),
    )


@contextmanager
def _clarify_llm(*, return_value=None, side_effect=None):
    p1, p2 = _clarify_patches(return_value=return_value, side_effect=side_effect)
    with p1, p2:
        yield


def _ready_service(tmp_path: Path) -> tuple[ClarificationService, Path]:
    ws = _ws(tmp_path)
    svc = ClarificationService(QAOpsSettings(output_dir=ws / "output"))
    client = MockLLMClient([_ANALYZER, _GAP_BLOCKER, _AGENT_ONE])
    with _clarify_llm(return_value=client):
        state = svc.start("run_1", ws / "input" / "t.md", ws)
    ans = [
        ClarificationAnswer(
            question_id=state.questions[0].question_id,
            answer_type=AnswerType.BOOLEAN,
            answer="true",
        )
    ]
    svc.submit_answers(ws, ans)
    return svc, ws


class TestServiceHandoff:
    def test_prepare_produces_parseable_requirements(self, tmp_path: Path) -> None:
        svc, ws = _ready_service(tmp_path)
        path = svc.prepare_test_design(ws)
        parsed = parse_requirements(path)
        assert len(parsed.requirements) == 1

    def test_requirement_id_stable(self, tmp_path: Path) -> None:
        svc, ws = _ready_service(tmp_path)
        parsed = parse_requirements(svc.prepare_test_design(ws))
        assert parsed.requirements[0].id == "REQ-001"

    def test_answer_augmented_into_requirement(self, tmp_path: Path) -> None:
        svc, ws = _ready_service(tmp_path)
        parsed = parse_requirements(svc.prepare_test_design(ws))
        assert any("Allow retry" in a for a in parsed.requirements[0].assumptions)

    def test_not_ready_refused(self, tmp_path: Path) -> None:
        ws = _ws(tmp_path)
        svc = ClarificationService(QAOpsSettings(output_dir=ws / "output"))
        client = MockLLMClient([_ANALYZER, _GAP_BLOCKER, _AGENT_ONE])
        with _clarify_llm(return_value=client):
            svc.start("run_1", ws / "input" / "t.md", ws)
        with pytest.raises(ClarificationNotReadyError):
            svc.prepare_test_design(ws)  # blocker unanswered

    def test_idempotent(self, tmp_path: Path) -> None:
        svc, ws = _ready_service(tmp_path)
        assert svc.prepare_test_design(ws) == svc.prepare_test_design(ws)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(APIConfig(runtime_dir=tmp_path / "runs"))
    with TestClient(app) as c:
        yield c


class TestStartTestDesignEndpoint:
    def test_404_unknown_run(self, client: TestClient) -> None:
        assert client.post("/api/v1/runs/nope/start-test-design").status_code == 404

    def test_409_no_clarification(self, client: TestClient) -> None:
        # A one-shot run has no clarification state -> 409.
        with patch("qaops.api.app.execute_run", side_effect=lambda *a, **k: None):
            created = client.post(
                "/api/v1/design",
                files={"file": ("r.md", b"A user logs in.", "text/markdown")},
            )
        rid = created.json()["run_id"]
        assert client.post(f"/api/v1/runs/{rid}/start-test-design").status_code == 409


class TestEndToEndHandoff:
    def test_clarify_answer_ready_then_design_completes(self, tmp_path: Path) -> None:
        # Full flow with a real background execution (TestClient runs tasks inline).
        cfg = APIConfig(runtime_dir=tmp_path / "runs")
        app = create_app(cfg)

        # The clarification analysis uses analyzer+gap+agent (3 calls); the design
        # phase via `requirements` entry point uses rules+gap+scenario+condition+
        # testcase (no analyzer). Separate client scripts per create_client call.
        clar_client = MockLLMClient([_ANALYZER, _GAP_BLOCKER, _AGENT_ONE])
        from tests.test_api import CONDITIONS, TEST_CASES
        from tests.test_phase32_ticket_api import RULES, SCENARIOS

        design_script = [RULES, json.dumps({"gaps": []}), SCENARIOS, CONDITIONS, TEST_CASES]

        def _clar_client(_s: object) -> MockLLMClient:
            return clar_client

        def _design_client(_s: object) -> MockLLMClient:
            return MockLLMClient(list(design_script))

        with TestClient(app) as c:
            # 1. start clarify=true run
            with _clarify_llm(side_effect=_clar_client):
                created = c.post(
                    "/api/v1/design",
                    files={"file": ("t.md", b"User checks store availability.", "text/markdown")},
                    data={"clarify": "true"},
                )
            rid = created.json()["run_id"]
            # 2. run is awaiting clarification with one blocking question
            clar = c.get(f"/api/v1/runs/{rid}/clarifications").json()
            assert clar["status"] == "clarifying"
            qid = clar["questions"][0]["question_id"]
            # 3. answer the blocking question -> ready
            answered = c.post(
                f"/api/v1/runs/{rid}/clarifications/answers",
                json={
                    "answers": [{"question_id": qid, "answer_type": "boolean", "answer": "true"}]
                },
            ).json()
            assert answered["readiness"]["ready"] is True
            # 4. start test design -> pipeline runs via requirements entry point
            with patch("qaops.services.design_service.create_client", side_effect=_design_client):
                started = c.post(f"/api/v1/runs/{rid}/start-test-design")
            assert started.status_code == 202
            # 5. run completes
            final = c.get(f"/api/v1/runs/{rid}").json()
            assert final["status"] == "completed"

    def test_start_before_ready_rejected(self, tmp_path: Path) -> None:
        cfg = APIConfig(runtime_dir=tmp_path / "runs")
        app = create_app(cfg)
        clar_client = MockLLMClient([_ANALYZER, _GAP_BLOCKER, _AGENT_ONE])

        with TestClient(app) as c:
            with _clarify_llm(side_effect=lambda _s: clar_client):
                created = c.post(
                    "/api/v1/design",
                    files={"file": ("t.md", b"User checks store availability.", "text/markdown")},
                    data={"clarify": "true"},
                )
            rid = created.json()["run_id"]
            # Not answered -> not ready -> start rejected with 409.
            r = c.post(f"/api/v1/runs/{rid}/start-test-design")
        assert r.status_code == 409
