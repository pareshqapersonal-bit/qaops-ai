"""Phase 36B Part 2 tests: image sidecar persistence + run-path transport.

Proves the production transport: an uploaded image is persisted as a run sidecar,
reconstructed by DesignService, and delivered to the RequirementAnalyzer's LLM
request with byte-identical data. Text/document-only runs are unaffected (evidence
stays None). A corrupt or missing sidecar fails clearly, never silently dropping
visual evidence.
"""

import base64
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from qaops.api.app import create_app
from qaops.api.config import APIConfig
from qaops.config import QAOpsSettings
from qaops.ingestion.evidence_sidecar import (
    EvidenceSidecarError,
    load_evidence_package,
    sidecar_path,
    write_image_sidecar,
)
from qaops.llm import ImagePart, MockLLMClient
from qaops.llm.models import LLMRequest, LLMResponse, LLMUsage
from qaops.services.design_service import DesignService
from tests.test_phase32_ticket_api import _DOC_RESPONSES

REAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
PNG_B64 = base64.b64encode(REAL_PNG).decode()
JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 20).decode()


def _part(
    name: str = "a.png", order: int = 0, media: str = "image/png", data: str = PNG_B64
) -> ImagePart:
    return ImagePart(media_type=media, data=data, source_filename=name, order=order)


def _response(payload: str) -> LLMResponse:
    return LLMResponse(text=payload, model="m", usage=LLMUsage(input_tokens=1, output_tokens=1))


# -- Sidecar persistence + reload ---------------------------------------------


class TestSidecar:
    def test_write_creates_sidecar(self, tmp_path: Path) -> None:
        write_image_sidecar(tmp_path, [_part()])
        assert sidecar_path(tmp_path).exists()
        # The sidecar is NOT under input/ (single-file input contract preserved).
        assert "input" not in sidecar_path(tmp_path).parts

    def test_reload_reconstructs_image_part_exactly(self, tmp_path: Path) -> None:
        write_image_sidecar(tmp_path, [_part(name="login.png")])
        pkg = load_evidence_package(tmp_path)
        assert pkg is not None
        assert len(pkg.images) == 1
        assert pkg.images[0].data == PNG_B64
        assert base64.b64decode(pkg.images[0].data) == REAL_PNG
        assert pkg.images[0].source_filename == "login.png"
        assert pkg.images[0].media_type == "image/png"

    def test_multiple_images_preserve_order(self, tmp_path: Path) -> None:
        write_image_sidecar(
            tmp_path,
            [
                _part(name="first.png", order=0),
                _part(name="second.jpg", order=1, media="image/jpeg", data=JPEG_B64),
            ],
        )
        pkg = load_evidence_package(tmp_path)
        assert pkg is not None
        assert [p.source_filename for p in pkg.ordered_images()] == ["first.png", "second.jpg"]
        assert [p.order for p in pkg.ordered_images()] == [0, 1]

    def test_no_sidecar_returns_none(self, tmp_path: Path) -> None:
        assert load_evidence_package(tmp_path) is None

    def test_corrupt_sidecar_raises(self, tmp_path: Path) -> None:
        path = sidecar_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("{ this is not valid json", encoding="utf-8")
        with pytest.raises(EvidenceSidecarError):
            load_evidence_package(tmp_path)

    def test_malformed_entry_raises(self, tmp_path: Path) -> None:
        path = sidecar_path(tmp_path)
        path.parent.mkdir(parents=True)
        # Valid JSON, but an entry is not a valid ImagePart (bad media_type).
        path.write_text(
            '[{"media_type": "image/gif", "data": "x", "source_filename": "a.gif", "order": 0}]'
        )
        with pytest.raises(EvidenceSidecarError):
            load_evidence_package(tmp_path)


# -- DesignService reconstructs evidence and reaches the analyzer --------------


class _CapturingMock(MockLLMClient):
    """Records the images on the first request (the analyzer's), declares image support."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__(responses)
        self.first_request: LLMRequest | None = None

    @property
    def supports_images(self) -> bool:
        return True

    def complete(self, request: LLMRequest) -> LLMResponse:
        if self.first_request is None:
            self.first_request = request
        return super().complete(request)


def _workspace_with_input(tmp_path: Path, text: str = "The screen shows a login form.") -> Path:
    ws = tmp_path / "ws"
    (ws / "input").mkdir(parents=True)
    (ws / "output").mkdir(parents=True)
    (ws / "input" / "ticket.md").write_text(text, encoding="utf-8")
    return ws


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret-key-1234567890")
    # Phase 38: image-bearing runs require an image-capable provider (nvidia).
    monkeypatch.setenv("NVIDIA_API_KEY", "sk-test-nvidia-key")


class TestRunPathTransport:
    def test_evidence_reaches_analyzer_request(self, tmp_path: Path) -> None:
        ws = _workspace_with_input(tmp_path)
        write_image_sidecar(ws, [_part(name="login_screen.png")])
        mock = _CapturingMock([_response(r) for r in _DOC_RESPONSES])
        with patch("qaops.services.design_service.create_client", return_value=mock):
            DesignService().run(
                ws / "input" / "ticket.md",
                QAOpsSettings(provider="nvidia", output_dir=ws / "output"),
            )
        assert mock.first_request is not None
        images = mock.first_request.messages[0].images
        assert len(images) == 1
        assert images[0].source_filename == "login_screen.png"

    def test_image_bytes_identical_at_boundary(self, tmp_path: Path) -> None:
        # The CRITICAL end-to-end assertion: base64 is byte-identical at complete().
        ws = _workspace_with_input(tmp_path)
        write_image_sidecar(ws, [_part(name="a.png")])
        mock = _CapturingMock([_response(r) for r in _DOC_RESPONSES])
        with patch("qaops.services.design_service.create_client", return_value=mock):
            DesignService().run(
                ws / "input" / "ticket.md",
                QAOpsSettings(provider="nvidia", output_dir=ws / "output"),
            )
        assert mock.first_request is not None
        img = mock.first_request.messages[0].images[0]
        assert img.data == PNG_B64
        assert base64.b64decode(img.data) == REAL_PNG
        assert img.media_type == "image/png"

    def test_multiple_images_reach_analyzer_in_order(self, tmp_path: Path) -> None:
        ws = _workspace_with_input(tmp_path)
        write_image_sidecar(
            ws,
            [
                _part(name="one.png", order=0),
                _part(name="two.jpg", order=1, media="image/jpeg", data=JPEG_B64),
            ],
        )
        mock = _CapturingMock([_response(r) for r in _DOC_RESPONSES])
        with patch("qaops.services.design_service.create_client", return_value=mock):
            DesignService().run(
                ws / "input" / "ticket.md",
                QAOpsSettings(provider="nvidia", output_dir=ws / "output"),
            )
        assert mock.first_request is not None
        names = [p.source_filename for p in mock.first_request.messages[0].images]
        assert names == ["one.png", "two.jpg"]

    def test_no_sidecar_passes_no_images(self, tmp_path: Path) -> None:
        # A run with no sidecar must reach the analyzer with an empty image list -
        # the existing text-only path, unchanged.
        ws = _workspace_with_input(tmp_path)
        mock = _CapturingMock([_response(r) for r in _DOC_RESPONSES])
        with patch("qaops.services.design_service.create_client", return_value=mock):
            DesignService().run(ws / "input" / "ticket.md", QAOpsSettings(output_dir=ws / "output"))
        assert mock.first_request is not None
        assert mock.first_request.messages[0].images == []

    def test_corrupt_sidecar_fails_the_run(self, tmp_path: Path) -> None:
        ws = _workspace_with_input(tmp_path)
        path = sidecar_path(ws)
        path.parent.mkdir(parents=True)
        path.write_text("{ not json", encoding="utf-8")
        mock = _CapturingMock([_response(r) for r in _DOC_RESPONSES])
        with (
            patch("qaops.services.design_service.create_client", return_value=mock),
            pytest.raises(EvidenceSidecarError),
        ):
            DesignService().run(ws / "input" / "ticket.md", QAOpsSettings(output_dir=ws / "output"))


# -- Endpoint: image-bearing ticket persists a sidecar ------------------------


@pytest.fixture
def config(tmp_path: Path) -> APIConfig:
    return APIConfig(runtime_dir=tmp_path / "runs", cors_origins=["http://localhost:5173"])


@pytest.fixture
def client(config: APIConfig) -> Iterator[TestClient]:
    with TestClient(create_app(config)) as test_client:
        yield test_client


class TestEndpointPersistence:
    def test_image_ticket_writes_sidecar(self, client: TestClient, config: APIConfig) -> None:
        with patch(
            "qaops.services.design_service.create_client",
            return_value=MockLLMClient([_response(r) for r in _DOC_RESPONSES]),
        ):
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "Ratings", "description": "Show ratings."},
                files=[("attachment", ("login.png", REAL_PNG, "image/png"))],
            )
        assert r.status_code == 202
        ws = config.runtime_dir / r.json()["run_id"]
        pkg = load_evidence_package(ws)
        assert pkg is not None
        assert pkg.images[0].source_filename == "login.png"
        assert base64.b64decode(pkg.images[0].data) == REAL_PNG

    def test_document_only_ticket_writes_no_sidecar(
        self, client: TestClient, config: APIConfig
    ) -> None:
        with patch(
            "qaops.services.design_service.create_client",
            return_value=MockLLMClient([_response(r) for r in _DOC_RESPONSES]),
        ):
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "X", "description": "Y"},
                files=[("attachment", ("spec.txt", b"design text", "text/plain"))],
            )
        assert r.status_code == 202
        ws = config.runtime_dir / r.json()["run_id"]
        assert load_evidence_package(ws) is None

    def test_ticket_only_writes_no_sidecar(self, client: TestClient, config: APIConfig) -> None:
        with patch(
            "qaops.services.design_service.create_client",
            return_value=MockLLMClient([_response(r) for r in _DOC_RESPONSES]),
        ):
            r = client.post("/api/v1/design/ticket", data={"title": "X", "description": "Y"})
        assert r.status_code == 202
        ws = config.runtime_dir / r.json()["run_id"]
        assert load_evidence_package(ws) is None

    def test_mixed_doc_and_image_sidecar_has_only_images(
        self, client: TestClient, config: APIConfig
    ) -> None:
        with patch(
            "qaops.services.design_service.create_client",
            return_value=MockLLMClient([_response(r) for r in _DOC_RESPONSES]),
        ):
            r = client.post(
                "/api/v1/design/ticket",
                data={"title": "X", "description": "Y"},
                files=[
                    ("attachment", ("spec.txt", b"design text", "text/plain")),
                    ("attachment", ("login.png", REAL_PNG, "image/png")),
                ],
            )
        assert r.status_code == 202
        ws = config.runtime_dir / r.json()["run_id"]
        pkg = load_evidence_package(ws)
        assert pkg is not None
        assert [p.source_filename for p in pkg.images] == ["login.png"]
