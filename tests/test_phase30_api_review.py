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


class TestResumeProducesReview:
    """Bugfix regression: a failed -> resumed -> COMPLETED run must get a review.

    The review wiring was originally only on the fresh-run COMPLETED path; the
    resume COMPLETED path produced no review. This drives resume_run directly with
    a patched service and asserts the review field + standalone export are present.
    """

    def _seed_resumable_run(self, tmp_path: Path):
        from qaops.api.runs import RunStore

        store = RunStore(tmp_path / "runs")
        run = store.create("s.csv")
        # A resume needs an input file present in the run's input dir.
        (run.input_dir / "s.csv").write_bytes(b"title,category,requirement_ids\r\n")
        return store, run

    def _outcome_from_fixture(self):
        import json

        from qaops.entrypoints import EntryPoint
        from qaops.models import TestDesignResult
        from qaops.services.design_service import DesignOutcome

        fixture = Path(__file__).parent / "fixtures" / "phase29" / "auto_delete_result.json"
        result = TestDesignResult.model_validate(json.loads(fixture.read_text()))
        return DesignOutcome(
            result=result, entry_point=EntryPoint.DOCUMENT, detection=None, artifacts=[]
        )

    def test_resume_to_completion_produces_review(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        from qaops.api.runner import resume_run
        from qaops.config import QAOpsSettings

        store, run = self._seed_resumable_run(tmp_path)
        outcome = self._outcome_from_fixture()

        service = MagicMock()
        service.resume.return_value = outcome
        settings = QAOpsSettings(output_dir=run.output_dir, provider="mock")

        resume_run(store, run.id, settings, service)

        updated = store.get(run.id)
        assert updated is not None
        assert updated.status.value == "completed"
        # The bug: review was None after resume. It must now be populated.
        assert updated.review is not None
        assert "findings" in updated.review
        # And the standalone export must exist in the run output dir.
        assert (run.output_dir / "review_report.json").exists()

    def test_resume_review_matches_reviewer_output(self, tmp_path: Path) -> None:
        # The resume-path review must be the QualityReviewer's real output, not a stub.
        from unittest.mock import MagicMock

        from qaops.api.runner import resume_run
        from qaops.config import QAOpsSettings
        from qaops.review import QualityReviewer

        store, run = self._seed_resumable_run(tmp_path)
        outcome = self._outcome_from_fixture()
        expected = QualityReviewer().review(outcome.result).model_dump(mode="json")

        service = MagicMock()
        service.resume.return_value = outcome
        settings = QAOpsSettings(output_dir=run.output_dir, provider="mock")
        resume_run(store, run.id, settings, service)

        assert store.get(run.id).review == expected


class TestFreshRunReviewUnchanged:
    """Guard: the fresh-run review behaviour is unchanged by the resume fix."""

    def test_fresh_run_still_has_review(self, review_client: TestClient) -> None:
        run_id = _submit(review_client, "s.csv", SCENARIO_CSV, [CONDITIONS, TEST_CASES])
        status = review_client.get(f"/api/v1/runs/{run_id}").json()
        assert status["status"] == "completed"
        assert status["review"] is not None
        assert "review_report.json" in [
            a["name"]
            for a in review_client.get(f"/api/v1/runs/{run_id}/artifacts").json()["artifacts"]
        ]
