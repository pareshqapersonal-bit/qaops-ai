"""Phase 35 backend tests: ticket + optional design/reference attachment (ADR-050).

The ticket endpoint is multipart; an optional attachment is extracted via the
existing load_document ingestion and appended as a delimited evidence section, then
the combined single .md flows through the EXISTING DOCUMENT pipeline. Attachment
failures are client-facing 400s, never 500s. Without an attachment the ticket-only
pipeline path is preserved. Also pins the empty-AC omission normalization change.
"""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from qaops.api.app import create_app
from qaops.api.config import APIConfig
from qaops.api.schemas import TicketRequest
from qaops.ingestion.ticket_normalizer import append_reference_material, ticket_to_markdown
from qaops.llm import MockLLMClient
from tests.test_phase32_ticket_api import _DOC_RESPONSES


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


def _mock() -> object:
    return patch(
        "qaops.services.design_service.create_client",
        return_value=MockLLMClient(list(_DOC_RESPONSES)),
    )


def _input_text(config: APIConfig, run_id: str) -> str:
    return next((config.runtime_dir / run_id / "input").iterdir()).read_text(encoding="utf-8")


# -- Normalizer: empty-AC omission (pinned Phase 35 change) --------------------


class TestEmptyAcceptanceCriteriaOmitted:
    def test_empty_ac_section_omitted(self) -> None:
        md = ticket_to_markdown(TicketRequest(title="Add OTP login", description="Users log in."))
        assert "## Acceptance Criteria" not in md

    def test_ac_section_present_when_criteria_exist(self) -> None:
        md = ticket_to_markdown(
            TicketRequest(title="t", description="d", acceptance_criteria=["OTP is sent."])
        )
        assert "## Acceptance Criteria" in md
        assert "1. OTP is sent." in md


class TestReferenceMaterialFormat:
    def test_exact_evidence_section_shape(self) -> None:
        base = ticket_to_markdown(TicketRequest(title="t", description="d"))
        combined = append_reference_material(base, filename="design.pdf", text="Some design text.")
        assert "## Design / Reference Material\nSource: design.pdf\n\nSome design text." in combined


# -- Endpoint: ticket-only (no attachment) ------------------------------------


class TestTicketOnly:
    def test_ticket_only_multipart_returns_202(self, client: TestClient) -> None:
        with _mock():
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "Add OTP login", "description": "Users log in with OTP."},
            )
        assert r.status_code == 202

    def test_ticket_only_has_no_reference_section(
        self, client: TestClient, config: APIConfig
    ) -> None:
        with _mock():
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "Add OTP login", "description": "Users log in with OTP."},
            )
        text = _input_text(config, r.json()["run_id"])
        assert "Design / Reference Material" not in text

    def test_ticket_only_matches_normalizer_output(
        self, client: TestClient, config: APIConfig
    ) -> None:
        # The written .md equals the pure normalizer output (byte-identical
        # ticket-only path, for equivalent normalized input).
        with _mock():
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "Add OTP login", "description": "Users log in with OTP."},
            )
        text = _input_text(config, r.json()["run_id"])
        expected = ticket_to_markdown(
            TicketRequest(title="Add OTP login", description="Users log in with OTP.")
        )
        assert text == expected


# -- Endpoint: ticket + attachment (supported formats) ------------------------


class TestTicketWithAttachment:
    def test_txt_attachment_appends_verbatim_evidence(
        self, client: TestClient, config: APIConfig
    ) -> None:
        body = b"The PDP shows a star rating widget with a numeric review count."
        with _mock():
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "Ratings", "description": "Show ratings on PDP."},
                files={"attachment": ("design.txt", body, "text/plain")},
            )
        assert r.status_code == 202
        text = _input_text(config, r.json()["run_id"])
        assert "## Design / Reference Material" in text
        assert "Source: design.txt" in text
        assert "star rating widget with a numeric review count" in text

    def test_md_attachment(self, client: TestClient, config: APIConfig) -> None:
        with _mock():
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "t", "description": "d"},
                files={"attachment": ("notes.md", b"# Design\n\nRating flow.", "text/markdown")},
            )
        assert r.status_code == 202
        text = _input_text(config, r.json()["run_id"])
        assert "Source: notes.md" in text
        assert "Rating flow." in text

    def test_filename_recorded_not_in_run_provenance(
        self, client: TestClient, config: APIConfig
    ) -> None:
        # source_name stays ticket-anchored; the attachment name lives only in the
        # evidence section, not the run input filename.
        with _mock():
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "Ratings", "description": "d", "ticket_id": "NEW-1"},
                files={"attachment": ("spec.txt", b"design evidence", "text/plain")},
            )
        run_id = r.json()["run_id"]
        input_name = next((config.runtime_dir / run_id / "input").iterdir()).name
        assert "NEW-1" in input_name
        assert "spec" not in input_name


# -- Endpoint: attachment error paths (all 400) -------------------------------


class TestAttachmentErrors:
    def test_unsupported_suffix_400(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/design/ticket",
            data={"title": "t", "description": "d"},
            files={"attachment": ("data.csv", b"a,b,c", "text/csv")},
        )
        assert r.status_code == 400
        assert "Unsupported attachment type" in r.json()["detail"]

    def test_empty_attachment_400(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/design/ticket",
            data={"title": "t", "description": "d"},
            files={"attachment": ("design.txt", b"", "text/plain")},
        )
        assert r.status_code == 400
        assert "empty" in r.json()["detail"].lower()

    def test_no_extractable_text_400(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/design/ticket",
            data={"title": "t", "description": "d"},
            files={"attachment": ("design.txt", b"   \n \t  ", "text/plain")},
        )
        assert r.status_code == 400
        assert "no extractable text" in r.json()["detail"].lower()

    def test_loader_failure_400_not_500(self, client: TestClient) -> None:
        # A .docx that isn't a valid docx (or whose loader dependency is missing)
        # must surface as a clear 400, never a 500.
        r = client.post(
            "/api/v1/design/ticket",
            data={"title": "t", "description": "d"},
            files={
                "attachment": (
                    "broken.docx",
                    b"not a real docx",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert r.status_code == 400
        assert "could not be processed" in r.json()["detail"].lower()


class TestTicketValidation:
    def test_empty_description_422(self, client: TestClient) -> None:
        r = client.post("/api/v1/design/ticket", data={"title": "t", "description": ""})
        assert r.status_code == 422

    def test_missing_title_422(self, client: TestClient) -> None:
        r = client.post("/api/v1/design/ticket", data={"description": "d"})
        assert r.status_code == 422


# -- Pipeline behavior: attachment is evidence, routes DOCUMENT ----------------


class TestAttachmentIsEvidence:
    def test_run_completes_through_document_pipeline(
        self, client: TestClient, config: APIConfig
    ) -> None:
        with _mock():
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "Ratings", "description": "Show ratings."},
                files={"attachment": ("design.txt", b"The PDP shows ratings.", "text/plain")},
            )
        run_id = r.json()["run_id"]
        status = client.get(f"/api/v1/runs/{run_id}").json()
        assert status["status"] == "completed"
        assert status["entry_point"] == "document"

    def test_sparse_ticket_plus_attachment_yields_gaps_not_fabrication(
        self, client: TestClient, config: APIConfig
    ) -> None:
        # The attachment is document evidence; the pipeline (GapAnalyzer, mocked)
        # produces gaps rather than the endpoint fabricating requirements from it.
        from tests.test_phase32_ticket_api import _DOC_RESPONSES_WITH_GAP

        with patch(
            "qaops.services.design_service.create_client",
            return_value=MockLLMClient(list(_DOC_RESPONSES_WITH_GAP)),
        ):
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "Ratings", "description": "Show ratings."},
                files={"attachment": ("design.txt", b"Some design reference.", "text/plain")},
            )
        run_id = r.json()["run_id"]
        status = client.get(f"/api/v1/runs/{run_id}").json()
        assert status["status"] == "completed"
        assert any("gap" in f["code"] for f in status["review"]["findings"])


# -- Regression: existing document endpoint unchanged -------------------------


class TestDocumentEndpointRegression:
    def test_document_upload_still_works(self, client: TestClient) -> None:
        from tests.test_api import SCENARIO_CSV
        from tests.test_phase32_ticket_api import CONDITIONS, TEST_CASES

        with patch(
            "qaops.services.design_service.create_client",
            return_value=MockLLMClient([CONDITIONS, TEST_CASES]),
        ):
            r = client.post("/api/v1/design", files={"file": ("s.csv", SCENARIO_CSV, "text/csv")})
        assert r.status_code == 202
