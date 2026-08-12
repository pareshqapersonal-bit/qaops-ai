"""Phase 35B backend tests: multiple design/reference attachments (ADR-051).

The ticket endpoint's multipart "attachment" field now accepts 0, 1, or N files
(same field name as Phase 35A). Each is extracted via the existing load_document and
appended as its own evidence section in upload order. Attachment handling is strict:
any bad file fails the whole request with a 400 (never a silent skip, never a 500).
Ticket-only stays byte-identical to Phase 32; a single attachment stays byte-identical
to Phase 35A.
"""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from qaops.api.app import create_app
from qaops.api.config import APIConfig
from qaops.api.schemas import TicketRequest
from qaops.ingestion.ticket_normalizer import (
    AttachmentEvidence,
    append_reference_material,
    append_reference_materials,
    ticket_to_markdown,
)
from qaops.llm import MockLLMClient
from tests.test_phase32_ticket_api import _DOC_RESPONSES, _DOC_RESPONSES_WITH_GAP


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


# -- Normalizer: AttachmentEvidence + multi-section append --------------------


class TestAppendReferenceMaterials:
    def test_empty_list_returns_unchanged(self) -> None:
        base = ticket_to_markdown(TicketRequest(title="t", description="d"))
        assert append_reference_materials(base, []) == base

    def test_single_evidence_byte_identical_to_35a_helper(self) -> None:
        base = ticket_to_markdown(TicketRequest(title="t", description="d"))
        plural = append_reference_materials(base, [AttachmentEvidence("a.txt", "Design A.")])
        singular = append_reference_material(base, filename="a.txt", text="Design A.")
        assert plural == singular

    def test_order_preserved_and_one_section_per_file(self) -> None:
        base = ticket_to_markdown(TicketRequest(title="t", description="d"))
        out = append_reference_materials(
            base,
            [
                AttachmentEvidence("first.pdf", "Text one."),
                AttachmentEvidence("second.md", "Text two."),
                AttachmentEvidence("third.txt", "Text three."),
            ],
        )
        assert out.count("## Design / Reference Material") == 3
        assert out.index("first.pdf") < out.index("second.md") < out.index("third.txt")
        for name in ("first.pdf", "second.md", "third.txt"):
            assert f"Source: {name}" in out

    def test_duplicate_names_kept_as_distinct_sections(self) -> None:
        base = ticket_to_markdown(TicketRequest(title="t", description="d"))
        out = append_reference_materials(
            base, [AttachmentEvidence("a.txt", "One."), AttachmentEvidence("a.txt", "Two.")]
        )
        assert out.count("Source: a.txt") == 2
        assert "One." in out and "Two." in out


# -- Endpoint: multiple attachments -------------------------------------------


class TestMultipleAttachments:
    def test_two_attachments_both_sections_in_order(
        self, client: TestClient, config: APIConfig
    ) -> None:
        with _mock():
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "Ratings", "description": "Show ratings."},
                files=[
                    ("attachment", ("a.txt", b"Design doc A.", "text/plain")),
                    ("attachment", ("b.md", b"# Design B\nMore.", "text/markdown")),
                ],
            )
        assert r.status_code == 202
        text = _input_text(config, r.json()["run_id"])
        assert text.count("## Design / Reference Material") == 2
        assert text.index("a.txt") < text.index("b.md")
        assert "Design doc A." in text
        assert "More." in text

    def test_three_mixed_supported_formats(self, client: TestClient, config: APIConfig) -> None:
        with _mock():
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "t", "description": "d"},
                files=[
                    ("attachment", ("one.txt", b"Alpha.", "text/plain")),
                    ("attachment", ("two.md", b"Bravo.", "text/markdown")),
                    ("attachment", ("three.markdown", b"Charlie.", "text/markdown")),
                ],
            )
        assert r.status_code == 202
        text = _input_text(config, r.json()["run_id"])
        assert text.count("## Design / Reference Material") == 3


class TestSingleAndNoAttachmentCompatibility:
    def test_single_attachment_still_works(self, client: TestClient, config: APIConfig) -> None:
        with _mock():
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "X", "description": "Y"},
                files={"attachment": ("only.txt", b"Solo evidence.", "text/plain")},
            )
        assert r.status_code == 202
        text = _input_text(config, r.json()["run_id"])
        assert text.count("## Design / Reference Material") == 1
        assert "Solo evidence." in text

    def test_no_attachment_byte_identical_to_ticket_only(
        self, client: TestClient, config: APIConfig
    ) -> None:
        with _mock():
            r = client.post(
                "/api/v1/design/ticket", data={"title": "Add OTP login", "description": "Log in."}
            )
        text = _input_text(config, r.json()["run_id"])
        expected = ticket_to_markdown(TicketRequest(title="Add OTP login", description="Log in."))
        assert text == expected
        assert "Design / Reference Material" not in text


# -- Endpoint: strict failure (any bad file -> 400, no silent skip) -----------


class TestStrictFailure:
    def test_good_plus_unsupported_400_names_file(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/design/ticket",
            data={"title": "t", "description": "d"},
            files=[
                ("attachment", ("good.txt", b"ok", "text/plain")),
                ("attachment", ("bad.csv", b"a,b,c", "text/csv")),
            ],
        )
        assert r.status_code == 400
        assert "bad.csv" in r.json()["detail"]

    def test_good_plus_empty_400(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/design/ticket",
            data={"title": "t", "description": "d"},
            files=[
                ("attachment", ("good.txt", b"ok", "text/plain")),
                ("attachment", ("empty.txt", b"", "text/plain")),
            ],
        )
        assert r.status_code == 400
        assert "empty.txt" in r.json()["detail"]

    def test_good_plus_no_text_400(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/design/ticket",
            data={"title": "t", "description": "d"},
            files=[
                ("attachment", ("good.txt", b"ok", "text/plain")),
                ("attachment", ("blank.txt", b"   \n \t ", "text/plain")),
            ],
        )
        assert r.status_code == 400
        assert "blank.txt" in r.json()["detail"]

    def test_good_plus_loader_failure_400_not_500(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/design/ticket",
            data={"title": "t", "description": "d"},
            files=[
                ("attachment", ("good.txt", b"ok", "text/plain")),
                ("attachment", ("broken.docx", b"not a docx", "application/octet-stream")),
            ],
        )
        assert r.status_code == 400
        assert "broken.docx" in r.json()["detail"]

    def test_no_run_created_on_failure(self, client: TestClient, config: APIConfig) -> None:
        client.post(
            "/api/v1/design/ticket",
            data={"title": "t", "description": "d"},
            files=[
                ("attachment", ("good.txt", b"ok", "text/plain")),
                ("attachment", ("bad.csv", b"x", "text/csv")),
            ],
        )
        runs_root = config.runtime_dir
        # A 400 before run creation leaves no run directory behind.
        assert not runs_root.exists() or not any(runs_root.iterdir())


# -- Provenance + pipeline routing --------------------------------------------


class TestProvenanceAndPipeline:
    def test_source_name_ticket_anchored_not_attachment(
        self, client: TestClient, config: APIConfig
    ) -> None:
        with _mock():
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "Ratings", "description": "d", "ticket_id": "NEW-9"},
                files=[
                    ("attachment", ("spec.txt", b"design", "text/plain")),
                    ("attachment", ("extra.md", b"notes", "text/markdown")),
                ],
            )
        run_id = r.json()["run_id"]
        input_name = next((config.runtime_dir / run_id / "input").iterdir()).name
        assert "NEW-9" in input_name
        assert "spec" not in input_name and "extra" not in input_name

    def test_multi_attachment_run_routes_document_and_completes(
        self, client: TestClient, config: APIConfig
    ) -> None:
        with _mock():
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "Ratings", "description": "Show ratings."},
                files=[
                    ("attachment", ("a.txt", b"Alpha design.", "text/plain")),
                    ("attachment", ("b.txt", b"Beta design.", "text/plain")),
                ],
            )
        status = client.get(f"/api/v1/runs/{r.json()['run_id']}").json()
        assert status["status"] == "completed"
        assert status["entry_point"] == "document"

    def test_attachments_are_evidence_not_fabricated_requirements(
        self, client: TestClient, config: APIConfig
    ) -> None:
        with patch(
            "qaops.services.design_service.create_client",
            return_value=MockLLMClient(list(_DOC_RESPONSES_WITH_GAP)),
        ):
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "Ratings", "description": "Show ratings."},
                files=[
                    ("attachment", ("a.txt", b"Some reference.", "text/plain")),
                    ("attachment", ("b.txt", b"More reference.", "text/plain")),
                ],
            )
        status = client.get(f"/api/v1/runs/{r.json()['run_id']}").json()
        assert status["status"] == "completed"
        assert any("gap" in f["code"] for f in status["review"]["findings"])


class TestTicketValidationUnchanged:
    def test_empty_description_422_even_with_attachments(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/design/ticket",
            data={"title": "t", "description": ""},
            files={"attachment": ("a.txt", b"ok", "text/plain")},
        )
        assert r.status_code == 422
