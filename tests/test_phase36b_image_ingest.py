"""Phase 36B Part 1 tests: image attachment ingestion -> ImagePart.

Covers the isolated image helper (magic-byte validation, size/count/total limits,
ImagePart construction preserving media_type/filename/order/bytes) and the ticket
endpoint accepting PNG/JPEG attachments alongside the unchanged Phase 35B document
path. Images are constructed and validated here; persistence/threading into the
execution path is a later part and is NOT covered.
"""

import base64
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from qaops.api.app import create_app
from qaops.api.config import APIConfig
from qaops.ingestion.image_ingest import (
    MAX_IMAGE_BYTES,
    ImageValidationError,
    build_image_part,
    check_image_budget,
    is_image_suffix,
)
from qaops.llm import MockLLMClient
from tests.test_phase32_ticket_api import _DOC_RESPONSES

# A real 1x1 PNG and a minimal valid JPEG header.
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 20


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret-key-1234567890")


@pytest.fixture
def config(tmp_path: Path) -> APIConfig:
    return APIConfig(runtime_dir=tmp_path / "runs", cors_origins=["http://localhost:5173"])


@pytest.fixture
def client(config: APIConfig) -> Iterator[TestClient]:
    with TestClient(create_app(config)) as test_client:
        yield test_client


def _mock() -> object:
    return patch(
        "qaops.services.design_service.create_client",
        return_value=MockLLMClient(list(_DOC_RESPONSES)),
    )


def _input_text(config: APIConfig, run_id: str) -> str:
    return next((config.runtime_dir / run_id / "input").iterdir()).read_text(encoding="utf-8")


# -- Isolated helper: ImagePart construction ----------------------------------


class TestBuildImagePart:
    def test_valid_png_to_image_part(self) -> None:
        p = build_image_part(filename="login.png", suffix=".png", data=PNG_BYTES, order=0)
        assert p.media_type == "image/png"
        assert p.source_filename == "login.png"
        assert p.order == 0

    def test_valid_jpeg_to_image_part(self) -> None:
        p = build_image_part(filename="d.jpg", suffix=".jpg", data=JPEG_BYTES, order=2)
        assert p.media_type == "image/jpeg"
        assert p.order == 2

    def test_jpeg_extension_variants_map_to_jpeg(self) -> None:
        for suffix in (".jpg", ".jpeg"):
            p = build_image_part(filename=f"a{suffix}", suffix=suffix, data=JPEG_BYTES, order=0)
            assert p.media_type == "image/jpeg"

    def test_base64_contains_exact_original_bytes(self) -> None:
        p = build_image_part(filename="login.png", suffix=".png", data=PNG_BYTES, order=0)
        assert base64.b64decode(p.data) == PNG_BYTES

    def test_filename_preserved(self) -> None:
        p = build_image_part(filename="My Screenshot.png", suffix=".png", data=PNG_BYTES, order=0)
        assert p.source_filename == "My Screenshot.png"

    def test_empty_image_rejected(self) -> None:
        with pytest.raises(ImageValidationError, match="empty"):
            build_image_part(filename="e.png", suffix=".png", data=b"", order=0)

    def test_invalid_png_magic_rejected(self) -> None:
        with pytest.raises(ImageValidationError, match="signature"):
            build_image_part(filename="bad.png", suffix=".png", data=b"NOTPNG" * 4, order=0)

    def test_invalid_jpeg_magic_rejected(self) -> None:
        with pytest.raises(ImageValidationError, match="signature"):
            build_image_part(filename="bad.jpg", suffix=".jpg", data=b"\x89PNG\r\n\x1a\n", order=0)

    def test_unsupported_extension_rejected(self) -> None:
        with pytest.raises(ImageValidationError, match="Unsupported image type"):
            build_image_part(filename="x.webp", suffix=".webp", data=b"RIFFxxxxWEBP", order=0)

    def test_oversized_image_rejected(self) -> None:
        oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_IMAGE_BYTES
        with pytest.raises(ImageValidationError, match="per-image"):
            build_image_part(filename="big.png", suffix=".png", data=oversized, order=0)

    def test_is_image_suffix(self) -> None:
        assert is_image_suffix(".png")
        assert is_image_suffix(".JPG")
        assert not is_image_suffix(".pdf")


class TestImageBudget:
    def test_sixth_image_rejected(self) -> None:
        with pytest.raises(ImageValidationError, match="Too many"):
            check_image_budget(count=6, total_bytes=1000)

    def test_fifth_image_allowed(self) -> None:
        check_image_budget(count=5, total_bytes=1000)  # no raise

    def test_total_over_limit_rejected(self) -> None:
        with pytest.raises(ImageValidationError, match="Total image payload"):
            check_image_budget(count=3, total_bytes=26 * 1024 * 1024)


# -- Endpoint: image attachments ----------------------------------------------


class TestEndpointImageAcceptance:
    def test_ticket_with_png_accepted(self, client: TestClient) -> None:
        with _mock():
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "Ratings", "description": "Show ratings."},
                files=[("attachment", ("login.png", PNG_BYTES, "image/png"))],
            )
        assert r.status_code == 202

    def test_ticket_with_jpeg_accepted(self, client: TestClient) -> None:
        with _mock():
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "X", "description": "Y"},
                files=[("attachment", ("d.jpg", JPEG_BYTES, "image/jpeg"))],
            )
        assert r.status_code == 202

    def test_mixed_document_and_image(self, client: TestClient, config: APIConfig) -> None:
        with _mock():
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "X", "description": "Y"},
                files=[
                    ("attachment", ("spec.txt", b"design reference text", "text/plain")),
                    ("attachment", ("login.png", PNG_BYTES, "image/png")),
                    ("attachment", ("checkout.jpg", JPEG_BYTES, "image/jpeg")),
                ],
            )
        assert r.status_code == 202
        # The document still lands in the combined markdown; images do not.
        text = _input_text(config, r.json()["run_id"])
        assert "design reference text" in text
        assert "login.png" not in text  # images are not folded into the text document

    def test_empty_image_400(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/design/ticket",
            data={"title": "X", "description": "Y"},
            files=[("attachment", ("e.png", b"", "image/png"))],
        )
        assert r.status_code == 400
        assert "e.png" in r.json()["detail"]

    def test_invalid_png_magic_400(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/design/ticket",
            data={"title": "X", "description": "Y"},
            files=[("attachment", ("bad.png", b"NOTPNG" * 4, "image/png"))],
        )
        assert r.status_code == 400
        assert "bad.png" in r.json()["detail"]

    def test_invalid_jpeg_magic_400(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/design/ticket",
            data={"title": "X", "description": "Y"},
            files=[("attachment", ("bad.jpg", b"\x89PNG\r\n\x1a\n", "image/jpeg"))],
        )
        assert r.status_code == 400
        assert "bad.jpg" in r.json()["detail"]

    def test_unsupported_image_extension_400(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/design/ticket",
            data={"title": "X", "description": "Y"},
            files=[("attachment", ("x.webp", b"RIFFxxxxWEBP", "image/webp"))],
        )
        assert r.status_code == 400
        assert "x.webp" in r.json()["detail"]

    def test_oversized_image_400(self, client: TestClient) -> None:
        oversized = b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_IMAGE_BYTES
        r = client.post(
            "/api/v1/design/ticket",
            data={"title": "X", "description": "Y"},
            files=[("attachment", ("big.png", oversized, "image/png"))],
        )
        assert r.status_code == 400
        assert "big.png" in r.json()["detail"]

    def test_sixth_image_400(self, client: TestClient) -> None:
        files = [("attachment", (f"i{n}.png", PNG_BYTES, "image/png")) for n in range(6)]
        r = client.post(
            "/api/v1/design/ticket", data={"title": "X", "description": "Y"}, files=files
        )
        assert r.status_code == 400
        assert "Too many" in r.json()["detail"]

    def test_total_payload_over_limit_400(self, client: TestClient) -> None:
        big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (9 * 1024 * 1024)
        files = [("attachment", (f"b{n}.png", big, "image/png")) for n in range(3)]
        r = client.post(
            "/api/v1/design/ticket", data={"title": "X", "description": "Y"}, files=files
        )
        assert r.status_code == 400
        assert "Total image payload" in r.json()["detail"]

    def test_documents_do_not_count_toward_image_limit(self, client: TestClient) -> None:
        # Five images (the max) plus several documents must still be accepted.
        files = [("attachment", (f"i{n}.png", PNG_BYTES, "image/png")) for n in range(5)]
        files += [
            ("attachment", ("a.txt", b"doc a", "text/plain")),
            ("attachment", ("b.md", b"doc b", "text/markdown")),
        ]
        with _mock():
            r = client.post(
                "/api/v1/design/ticket", data={"title": "X", "description": "Y"}, files=files
            )
        assert r.status_code == 202


# -- Backward compatibility ---------------------------------------------------


class TestBackwardCompatibility:
    def test_ticket_only_unchanged(self, client: TestClient, config: APIConfig) -> None:
        from qaops.api.schemas import TicketRequest
        from qaops.ingestion.ticket_normalizer import ticket_to_markdown

        with _mock():
            r = client.post(
                "/api/v1/design/ticket", data={"title": "Add OTP login", "description": "Log in."}
            )
        assert r.status_code == 202
        text = _input_text(config, r.json()["run_id"])
        assert text == ticket_to_markdown(
            TicketRequest(title="Add OTP login", description="Log in.")
        )

    def test_single_document_attachment_unchanged(
        self, client: TestClient, config: APIConfig
    ) -> None:
        with _mock():
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "X", "description": "Y"},
                files={"attachment": ("only.txt", b"Solo evidence.", "text/plain")},
            )
        assert r.status_code == 202
        text = _input_text(config, r.json()["run_id"])
        assert "Solo evidence." in text
        assert text.count("## Design / Reference Material") == 1

    def test_multiple_document_attachments_unchanged(
        self, client: TestClient, config: APIConfig
    ) -> None:
        with _mock():
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "X", "description": "Y"},
                files=[
                    ("attachment", ("a.txt", b"Alpha.", "text/plain")),
                    ("attachment", ("b.md", b"Bravo.", "text/markdown")),
                ],
            )
        assert r.status_code == 202
        text = _input_text(config, r.json()["run_id"])
        assert text.count("## Design / Reference Material") == 2
