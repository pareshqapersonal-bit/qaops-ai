"""Phase 31 API tests: review_advice gating + surfacing (ADR-046).

Asserts the gating contract: OFF by default -> no review_advice field/export
(byte-identical); ON -> field present, export listed, status still COMPLETED.
"""

from contextlib import AbstractContextManager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from qaops.api.app import create_app
from qaops.api.config import APIConfig
from qaops.llm import MockLLMClient
from tests.test_api import CONDITIONS, SCENARIO_CSV, TEST_CASES


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret-key-1234567890")


def _client(tmp_path: Path, **settings_overrides: object) -> TestClient:
    cfg = APIConfig(runtime_dir=tmp_path / "runs", cors_origins=["http://localhost:5173"])
    app = create_app(cfg)
    return TestClient(app)


def _mock(responses: list[str]) -> AbstractContextManager[object]:
    return patch(
        "qaops.services.design_service.create_client",
        return_value=MockLLMClient(list(responses)),
    )


def _submit(client: TestClient, responses: list[str]) -> str:
    with _mock(responses):
        r = client.post("/api/v1/design", files={"file": ("s.csv", SCENARIO_CSV, "text/csv")})
    assert r.status_code == 202, r.text
    return str(r.json()["run_id"])


class TestGatingOffByDefault:
    def test_review_advice_absent_when_disabled(self, tmp_path: Path) -> None:
        # Default settings: review_advice_enabled is False.
        client = _client(tmp_path)
        with client:
            run_id = _submit(client, [CONDITIONS, TEST_CASES])
            status = client.get(f"/api/v1/runs/{run_id}").json()
            assert status["status"] == "completed"
            # Field exists in the contract but is null (backward compatible).
            assert status.get("review_advice") is None
            arts = client.get(f"/api/v1/runs/{run_id}/artifacts").json()["artifacts"]
            names = [a["name"] for a in arts]
            assert "review_advice.json" not in names
            # The Phase 30 review IS still present (unchanged behavior).
            assert status["review"] is not None


class TestGatingOnSurfacesAdvice:
    def test_review_advice_present_when_enabled(self, tmp_path: Path) -> None:
        from qaops.cli.config_loader import load_settings

        enabled = load_settings(None).model_copy(update={"review_advice_enabled": True})
        client = _client(tmp_path)
        with client, patch("qaops.api.app.load_settings", return_value=enabled):
            run_id = _submit(client, [CONDITIONS, TEST_CASES])
            status = client.get(f"/api/v1/runs/{run_id}").json()
            assert status["status"] == "completed"
            advice = status.get("review_advice")
            assert advice is not None
            assert "headline" in advice
            assert advice["generated_by"] in ("deterministic", "llm")
            arts = client.get(f"/api/v1/runs/{run_id}/artifacts").json()["artifacts"]
            names = [a["name"] for a in arts]
            assert "review_advice.json" in names
