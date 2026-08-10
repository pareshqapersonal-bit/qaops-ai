"""Phase 32 API/integration tests: ticket endpoint + shared run creation (ADR-047).

Proves the ticket enters the EXISTING DOCUMENT pipeline via the shared
run-creation helper, that provenance is preserved, that a sparse ticket yields
genuine gaps (not fabricated requirements), and that the document-upload path is
unchanged after the helper extraction.
"""

import json
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

# Full DOCUMENT-path canned LLM responses (6 stages call the LLM; coverage is
# deterministic). Shapes mirror the existing multi-entry/CLI test fixtures.
ANALYZER = json.dumps(
    {
        "requirements": [
            {"title": "OTP login", "description": "User logs in with OTP.", "actors": ["User"]},
        ]
    }
)
RULES = json.dumps(
    {"rules": [{"requirement_id": "REQ-001", "rule": "a rule", "source_excerpt": ""}]}
)
GAPS_EMPTY = json.dumps({"gaps": []})
GAPS_ONE = json.dumps(
    {
        "gaps": [
            {
                "description": "OTP expiry unspecified.",
                "severity": "blocker",
                "requirement_id": "REQ-001",
                "suggested_question": "How long until the OTP expires?",
            }
        ]
    }
)
SCENARIOS = json.dumps(
    {
        "scenarios": [
            {
                "title": "valid login",
                "description": "d",
                "category": "positive",
                "requirement_ids": ["REQ-001"],
            }
        ]
    }
)

_DOC_RESPONSES = [ANALYZER, RULES, GAPS_EMPTY, SCENARIOS, CONDITIONS, TEST_CASES]
_DOC_RESPONSES_WITH_GAP = [ANALYZER, RULES, GAPS_ONE, SCENARIOS, CONDITIONS, TEST_CASES]

_OTP_BODY = {
    "title": "Add OTP login",
    "description": "Users should be able to log in using their mobile number and OTP.",
    "acceptance_criteria": [
        "OTP should be sent to the registered mobile number.",
        "Valid OTP should log the user in.",
    ],
    "ticket_id": "OTP-123",
    "priority": "High",
    "labels": ["auth", "login"],
}


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret-key-1234567890")


@pytest.fixture
def config(tmp_path: Path) -> APIConfig:
    return APIConfig(runtime_dir=tmp_path / "runs", cors_origins=["http://localhost:5173"])


@pytest.fixture
def client(config: APIConfig) -> Iterator[TestClient]:
    app = create_app(config)
    with TestClient(app) as test_client:
        yield test_client


def _mock(responses: list[str]) -> AbstractContextManager[object]:
    return patch(
        "qaops.services.design_service.create_client",
        return_value=MockLLMClient(list(responses)),
    )


class TestTicketEndpoint:
    def test_returns_202_and_run_id(self, client: TestClient) -> None:
        with _mock(_DOC_RESPONSES):
            r = client.post("/api/v1/design/ticket", json=_OTP_BODY)
        assert r.status_code == 202
        assert "run_id" in r.json()

    def test_markdown_written_to_input_dir(self, client: TestClient, config: APIConfig) -> None:
        with _mock(_DOC_RESPONSES):
            r = client.post("/api/v1/design/ticket", json=_OTP_BODY)
        run_id = r.json()["run_id"]
        input_files = list((config.runtime_dir / run_id / "input").iterdir())
        assert len(input_files) == 1
        assert input_files[0].suffix == ".md"

    def test_provenance_in_filename(self, client: TestClient, config: APIConfig) -> None:
        with _mock(_DOC_RESPONSES):
            r = client.post("/api/v1/design/ticket", json=_OTP_BODY)
        run_id = r.json()["run_id"]
        name = next((config.runtime_dir / run_id / "input").iterdir()).name
        assert "OTP-123" in name
        assert "Add OTP login" in name

    def test_fallback_provenance_without_ticket_id(
        self, client: TestClient, config: APIConfig
    ) -> None:
        body = {k: v for k, v in _OTP_BODY.items() if k != "ticket_id"}
        with _mock(_DOC_RESPONSES):
            r = client.post("/api/v1/design/ticket", json=body)
        run_id = r.json()["run_id"]
        name = next((config.runtime_dir / run_id / "input").iterdir()).name
        assert "Add OTP login" in name

    def test_invalid_ticket_is_422(self, client: TestClient) -> None:
        # Missing required description.
        r = client.post("/api/v1/design/ticket", json={"title": "x", "acceptance_criteria": []})
        assert r.status_code == 422


class TestTicketThroughDocumentPipeline:
    def test_ticket_run_completes_with_all_artifacts(
        self, client: TestClient, config: APIConfig
    ) -> None:
        with _mock(_DOC_RESPONSES):
            r = client.post("/api/v1/design/ticket", json=_OTP_BODY)
        run_id = r.json()["run_id"]
        status = client.get(f"/api/v1/runs/{run_id}").json()
        assert status["status"] == "completed"
        # Phase 30 review still runs for a ticket-sourced run, and provenance
        # reaches the artifacts: source_name derives from the .md filename.
        assert status["review"] is not None
        assert "OTP-123" in status["review"]["source_name"]

    def test_routed_through_document_entry_point(
        self, client: TestClient, config: APIConfig
    ) -> None:
        with _mock(_DOC_RESPONSES):
            r = client.post("/api/v1/design/ticket", json=_OTP_BODY)
        run_id = r.json()["run_id"]
        status = client.get(f"/api/v1/runs/{run_id}").json()
        # The document entry point is what a .md prose file classifies as.
        assert status.get("entry_point") == "document"

    def test_sparse_ticket_yields_gap_not_fabrication(
        self, client: TestClient, config: APIConfig
    ) -> None:
        # A sparse ticket (no expiry rule). The gap comes from the pipeline's own
        # GapAnalyzer (mocked to return one), proving missing info flows through as
        # a genuine gap rather than the normalizer inventing a requirement.
        sparse = {
            "title": "Add OTP login",
            "description": "Users log in with OTP.",
            "acceptance_criteria": [],
        }
        with _mock(_DOC_RESPONSES_WITH_GAP):
            r = client.post("/api/v1/design/ticket", json=sparse)
        run_id = r.json()["run_id"]
        status = client.get(f"/api/v1/runs/{run_id}").json()
        assert status["status"] == "completed"
        # The review surfaces the blocker gap the pipeline produced.
        review = status["review"]
        assert any("gap" in f["code"] for f in review["findings"])


class TestDocumentUploadRegression:
    def test_document_upload_still_works(self, client: TestClient) -> None:
        # The scenarios CSV path (unchanged) must behave exactly as before.
        with _mock([CONDITIONS, TEST_CASES]):
            r = client.post("/api/v1/design", files={"file": ("s.csv", SCENARIO_CSV, "text/csv")})
        assert r.status_code == 202
        run_id = r.json()["run_id"]
        status = client.get(f"/api/v1/runs/{run_id}").json()
        assert status["status"] == "completed"

    def test_unsupported_suffix_still_400(self, client: TestClient) -> None:
        r = client.post("/api/v1/design", files={"file": ("x.exe", b"data", "application/octet")})
        assert r.status_code == 400

    def test_empty_upload_still_400(self, client: TestClient) -> None:
        r = client.post("/api/v1/design", files={"file": ("s.csv", b"", "text/csv")})
        assert r.status_code == 400
