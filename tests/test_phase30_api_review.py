"""Phase 30 API tests: review field + standalone export on COMPLETED runs.

Drives a real run through the runner (mock LLM) and asserts the additive
surfacing, backward compatibility, and that the review is advisory (never changes
status). Self-contained fixtures to avoid cross-module fixture re-import.
"""

from collections.abc import Iterator
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


@pytest.fixture
def review_client(tmp_path: Path) -> Iterator[TestClient]:
    cfg = APIConfig(runtime_dir=tmp_path / "runs", cors_origins=["http://localhost:5173"])
    app = create_app(cfg)
    with TestClient(app) as test_client:
        yield test_client


def _mock_client(responses: list[str]) -> AbstractContextManager[object]:
    return patch(
        "qaops.services.design_service.create_client",
        return_value=MockLLMClient(list(responses)),
    )


def _submit(client: TestClient, name: str, data: bytes, responses: list[str]) -> str:
    with _mock_client(responses):
        response = client.post("/api/v1/design", files={"file": (name, data, "text/csv")})
    assert response.status_code == 202, response.text
    return str(response.json()["run_id"])


class TestReviewSurfacing:
    def test_completed_run_has_review_field(self, review_client: TestClient) -> None:
        run_id = _submit(review_client, "s.csv", SCENARIO_CSV, [CONDITIONS, TEST_CASES])
        status = review_client.get(f"/api/v1/runs/{run_id}").json()
        assert status["status"] == "completed"
        assert status["review"] is not None
        review = status["review"]
        assert "findings" in review
        assert "observations" in review
        assert "recommendations" in review

    def test_review_export_is_listed(self, review_client: TestClient) -> None:
        run_id = _submit(review_client, "s.csv", SCENARIO_CSV, [CONDITIONS, TEST_CASES])
        arts = review_client.get(f"/api/v1/runs/{run_id}/artifacts").json()["artifacts"]
        names = [a["name"] for a in arts]
        assert "review_report.json" in names

    def test_review_is_advisory_status_still_completed(self, review_client: TestClient) -> None:
        run_id = _submit(review_client, "s.csv", SCENARIO_CSV, [CONDITIONS, TEST_CASES])
        status = review_client.get(f"/api/v1/runs/{run_id}").json()
        assert status["status"] == "completed"

    def test_review_field_present_in_contract(self, review_client: TestClient) -> None:
        run_id = _submit(review_client, "s.csv", SCENARIO_CSV, [CONDITIONS, TEST_CASES])
        status = review_client.get(f"/api/v1/runs/{run_id}").json()
        assert "review" in status
