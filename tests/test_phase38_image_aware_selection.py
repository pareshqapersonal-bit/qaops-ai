"""Phase 38 tests: NVIDIA registry registration + image-aware provider selection.

Proves the two fixes: (1) nvidia is a real registry provider so QAOPS_PROVIDER=nvidia
leads the chain and appears in available_providers() when NVIDIA_API_KEY is set;
(2) image-bearing runs only consider image-capable providers, never recover onto
text-only ones, and fail fast with a clear message when none is available - while
text-only runs keep the existing multi-provider fallback unchanged.
"""

import base64

import pytest

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.execution.executor import AdaptiveExecutor
from qaops.execution.models import ModelInfo
from qaops.execution.registry import available_providers, get_provider
from qaops.execution.selector import StageRequirements, _passes_filter
from qaops.llm import ImagePart, MockLLMClient
from qaops.llm.models import LLMResponse, LLMUsage
from qaops.services.design_service import fallback_providers


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "sk-test-nvidia")
    monkeypatch.setenv("GEMINI_API_KEY", "sk-test-gemini")
    monkeypatch.setenv("GROQ_API_KEY", "sk-test-groq")


# -- Registry gap fix ---------------------------------------------------------


class TestRegistry:
    def test_get_provider_nvidia_returns_info(self) -> None:
        info = get_provider("nvidia")
        assert info is not None
        assert info.name == "nvidia"
        assert info.images is True
        assert info.key_variables == ("NVIDIA_API_KEY",)

    def test_nvidia_in_available_providers_when_key_set(self) -> None:
        assert any(p.name == "nvidia" for p in available_providers())

    def test_nvidia_absent_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        assert all(p.name != "nvidia" for p in available_providers())

    def test_qaops_provider_nvidia_leads_chain(self) -> None:
        chain = [p.name for p in fallback_providers(QAOpsSettings(provider="nvidia"))]
        assert chain[0] == "nvidia"

    def test_existing_provider_leads_chain_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        chain = [p.name for p in fallback_providers(QAOpsSettings(provider="anthropic"))]
        assert chain[0] == "anthropic"

    def test_registry_image_flag_matches_client_capability(self) -> None:
        # The registry's static image flag must agree with the NVIDIA client's
        # runtime supports_images, since they describe the same fact in two layers.
        from qaops.llm.nvidia_client import NvidiaClient

        registry_flag = get_provider("nvidia").images  # type: ignore[union-attr]
        client_flag = NvidiaClient(model="m", sdk_client=object()).supports_images  # type: ignore[arg-type]
        assert registry_flag == client_flag is True


# -- Selector image-capability filtering --------------------------------------


class TestSelectorFiltering:
    def _text_model(self) -> ModelInfo:
        return ModelInfo(name="text-x", provider="gemini")

    def _image_model(self) -> ModelInfo:
        return ModelInfo(name="nemotron", provider="nvidia", images_supported=True)

    def test_image_run_rejects_text_only_model(self) -> None:
        ok, reason = _passes_filter(self._text_model(), StageRequirements(needs_images=True), set())
        assert ok is False
        assert "image" in reason.lower()

    def test_image_run_accepts_image_model(self) -> None:
        ok, _ = _passes_filter(self._image_model(), StageRequirements(needs_images=True), set())
        assert ok is True

    def test_text_run_accepts_text_model_unchanged(self) -> None:
        ok, _ = _passes_filter(self._text_model(), StageRequirements(needs_images=False), set())
        assert ok is True

    def test_text_run_accepts_image_model_too(self) -> None:
        # A text run does not exclude image-capable providers; it just doesn't require them.
        ok, _ = _passes_filter(self._image_model(), StageRequirements(needs_images=False), set())
        assert ok is True

    def test_default_requirements_have_needs_images_false(self) -> None:
        assert StageRequirements().needs_images is False


# -- Executor selection scenarios ---------------------------------------------


def _noop_factory(_settings: object) -> list:
    return []


class TestExecutorSelection:
    def _settings(self) -> QAOpsSettings:
        return QAOpsSettings(provider="nvidia")

    def test_image_run_selects_nvidia(self) -> None:
        providers = [get_provider("nvidia"), get_provider("groq"), get_provider("gemini")]
        ex = AdaptiveExecutor(
            [p for p in providers if p], self._settings(), _noop_factory, requires_images=True
        )
        assert ex._select_first_provider().name == "nvidia"

    def test_image_run_without_capable_provider_fails_clearly(self) -> None:
        providers = [get_provider("gemini"), get_provider("groq")]
        ex = AdaptiveExecutor(
            [p for p in providers if p], self._settings(), _noop_factory, requires_images=True
        )
        with pytest.raises(StageError) as exc:
            ex._select_first_provider()
        message = str(exc.value)
        assert "image evidence" in message
        assert "nvidia" in message.lower()

    def test_image_run_never_recovers_onto_text_only(self) -> None:
        # With image capability required, text-only providers yield no candidates,
        # so they can never be selected as recovery targets.
        providers = [get_provider("gemini"), get_provider("groq")]
        ex = AdaptiveExecutor(
            [p for p in providers if p], self._settings(), _noop_factory, requires_images=True
        )
        assert ex._candidates(get_provider("gemini")) == []  # type: ignore[arg-type]
        assert ex._candidates(get_provider("groq")) == []  # type: ignore[arg-type]

    def test_text_run_uses_existing_fallback_unchanged(self) -> None:
        # A text run with only text-only providers selects the first normally and
        # they remain valid recovery candidates - existing behavior.
        providers = [get_provider("gemini"), get_provider("groq")]
        ex = AdaptiveExecutor(
            [p for p in providers if p], self._settings(), _noop_factory, requires_images=False
        )
        assert ex._select_first_provider().name in {"gemini", "groq"}
        assert ex._candidates(get_provider("gemini")) != []  # type: ignore[arg-type]

    def test_document_run_default_requires_images_false(self) -> None:
        # Proof point 8: a run built WITHOUT the requires_images flag (the default,
        # as every non-image/PRD run is) imposes no image requirement, so text-only
        # providers remain fully eligible - the document flow is unchanged.
        providers = [get_provider("gemini"), get_provider("groq")]
        ex = AdaptiveExecutor([p for p in providers if p], self._settings(), _noop_factory)
        assert ex._requirements().needs_images is False
        assert ex._candidates(get_provider("gemini")) != []  # type: ignore[arg-type]
        assert ex._select_first_provider().name in {"gemini", "groq"}


# -- End-to-end: image ticket selects NVIDIA and transport reaches the client -


class _CapturingMock(MockLLMClient):
    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__(responses)
        self.first_request = None

    @property
    def supports_images(self) -> bool:
        return True

    def complete(self, request: object) -> LLMResponse:
        if self.first_request is None:
            self.first_request = request
        return super().complete(request)  # type: ignore[arg-type]


class TestEndToEndSelection:
    def test_image_run_selects_nvidia_and_transport_reaches_client(
        self, tmp_path: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import patch

        from qaops.ingestion.evidence_sidecar import write_image_sidecar
        from qaops.services.design_service import DesignService
        from tests.test_phase32_ticket_api import _DOC_RESPONSES

        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        b64 = base64.b64encode(png).decode()
        ws = tmp_path / "ws"  # type: ignore[operator]
        (ws / "input").mkdir(parents=True)
        (ws / "output").mkdir(parents=True)
        (ws / "input" / "ticket.md").write_text("The screen shows a login form.")
        write_image_sidecar(
            ws, [ImagePart(media_type="image/png", data=b64, source_filename="login.png", order=0)]
        )

        captured = {}

        def _fake_create_client(settings: QAOpsSettings) -> _CapturingMock:
            captured["provider"] = settings.provider
            return _CapturingMock(
                [
                    LLMResponse(text=r, model="m", usage=LLMUsage(input_tokens=1, output_tokens=1))
                    for r in _DOC_RESPONSES
                ]
            )

        mock_holder = {}
        original = _fake_create_client

        def _capture(settings: QAOpsSettings) -> _CapturingMock:
            client = original(settings)
            mock_holder["client"] = client
            return client

        with patch("qaops.services.design_service.create_client", side_effect=_capture):
            DesignService().run(
                ws / "input" / "ticket.md",
                QAOpsSettings(provider="nvidia", output_dir=ws / "output"),
            )

        # The run selected the nvidia provider (image-capable) ...
        assert captured["provider"] == "nvidia"
        # ... and the image transport reached the client's first (analyzer) request.
        client = mock_holder["client"]
        assert client.first_request is not None
        images = client.first_request.messages[0].images
        assert len(images) == 1
        assert base64.b64decode(images[0].data) == png

    def test_document_run_no_evidence_completes_unchanged(
        self, tmp_path: object, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Proof point 8 (end-to-end): a document run with NO image sidecar reaches
        # the analyzer with an empty image list and completes normally - the working
        # PRD/document flow is unchanged and never requires an image-capable provider.
        from unittest.mock import patch

        from qaops.services.design_service import DesignService
        from tests.test_phase32_ticket_api import _DOC_RESPONSES

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        ws = tmp_path / "ws"  # type: ignore[operator]
        (ws / "input").mkdir(parents=True)
        (ws / "output").mkdir(parents=True)
        (ws / "input" / "prd.md").write_text("The system shall let a user log in with OTP.")
        # No write_image_sidecar -> no evidence.

        captured: dict = {}

        def _capture(settings: QAOpsSettings) -> _CapturingMock:
            captured["provider"] = settings.provider
            client = _CapturingMock(
                [
                    LLMResponse(text=r, model="m", usage=LLMUsage(input_tokens=1, output_tokens=1))
                    for r in _DOC_RESPONSES
                ]
            )
            captured["client"] = client
            return client

        with patch("qaops.services.design_service.create_client", side_effect=_capture):
            outcome = DesignService().run(
                ws / "input" / "prd.md",
                QAOpsSettings(provider="anthropic", output_dir=ws / "output"),
            )

        # A text provider served it, the analyzer request carried NO images, and the
        # run produced a result - identical to pre-Phase-38 behavior.
        assert captured["provider"] == "anthropic"
        assert captured["client"].first_request.messages[0].images == []
        assert outcome is not None
