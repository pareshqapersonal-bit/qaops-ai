"""Phase 39 tests: NVIDIA classified free so it survives FREE_ONLY for image runs.

Option A from the ADR-055 review: NVIDIA's Nemotron endpoint is free (no per-call
cost) by this codebase's cost-based definition, so a configured NVIDIA model is
free-eligible. This keeps image-bearing runs working under
QAOPS_EXECUTION_STRATEGY=free_only without changing the strategy engine, provider
priority (stays 60), or the image transport. Text/PRD ordering is unchanged: NVIDIA
still ranks behind the existing free providers and is never preferred for text runs.
"""

import base64

import pytest

from qaops.config import QAOpsSettings
from qaops.execution.executor import AdaptiveExecutor
from qaops.execution.registry import available_providers, get_provider
from qaops.llm import ImagePart, MockLLMClient
from qaops.llm.models import LLMResponse, LLMUsage


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "sk-test-nvidia")
    monkeypatch.setenv("GEMINI_API_KEY", "sk-test-gemini")
    monkeypatch.setenv("GROQ_API_KEY", "sk-test-groq")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-openrouter")


def _providers() -> list:
    return [get_provider(p.name) for p in available_providers()]


def _noop_factory(_settings: object) -> list:
    return []


_STAGE_NAMES = ("requirement_analyzer", "business_rule_extractor", "gap_analyzer")


def _image_ex(providers, settings, *, at_stage="requirement_analyzer"):
    ex = AdaptiveExecutor(
        providers,
        settings,
        _noop_factory,
        image_stage_name="requirement_analyzer",
        stage_names=_STAGE_NAMES,
    )
    ex._current_stage_name = at_stage
    return ex


class TestNvidiaFreeClassification:
    def test_nvidia_configured_model_is_free(self) -> None:
        ex = _image_ex(
            [get_provider("nvidia")],
            QAOpsSettings(provider="nvidia", execution_strategy="free_only"),
        )
        assert ex._configured_model_is_free("nvidia") is True

    def test_nvidia_priority_unchanged_at_60(self) -> None:
        assert get_provider("nvidia").priority == 60  # type: ignore[union-attr]


class TestFreeOnlyImageSelection:
    def test_free_only_image_run_selects_nvidia(self) -> None:
        # Phase B: gemini-flash is now a free, image-capable candidate. Under the
        # existing (unchanged) provider chain gemini precedes nvidia, so the first
        # capable provider selected for the image stage is gemini - both are eligible
        # and this is the approved capability-driven reality (no NVIDIA-first rule).
        ex = _image_ex(
            _providers(),
            QAOpsSettings(provider="nvidia", execution_strategy="free_only"),
        )
        first = ex._select_first_provider().name
        assert first in {"gemini", "nvidia"}  # an image-capable, free provider
        # gemini appears earlier in the existing chain, so it is preferred.
        assert first == "gemini"

    def test_free_only_image_run_does_not_fall_back_to_text_only(self) -> None:
        # Image runs never fall back to text-only providers. Phase B makes gemini-flash
        # image-capable, so the image-capable set is now {nvidia, gemini}; every OTHER
        # (genuinely text-only) provider still yields no image candidate.
        ex = _image_ex(
            _providers(),
            QAOpsSettings(provider="nvidia", execution_strategy="free_only"),
        )
        image_capable = {"nvidia", "gemini"}
        for provider in _providers():
            if provider.name not in image_capable:
                assert ex._candidates(provider) == []


class TestTextOrderingUnchanged:
    def test_free_only_text_run_does_not_prefer_nvidia(self) -> None:
        # A PRD/text run under free_only must still lead with the existing free
        # providers; nvidia (priority 60) must not become the preferred provider.
        ex = AdaptiveExecutor(
            _providers(),
            QAOpsSettings(provider="gemini", execution_strategy="free_only"),
            _noop_factory,
        )
        assert ex._select_first_provider().name != "nvidia"

    def test_nvidia_ranks_last_among_free_providers(self) -> None:
        ex = AdaptiveExecutor(
            _providers(),
            QAOpsSettings(provider="gemini", execution_strategy="free_only"),
            _noop_factory,
        )
        order = [p.name for p in ex._providers]
        assert "nvidia" in order
        assert order.index("nvidia") == len(order) - 1  # last

    def test_existing_free_provider_order_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Without the NVIDIA key, the text-run order among existing providers is the
        # pre-change world; the fix must not perturb it.
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        ex = AdaptiveExecutor(
            _providers(),
            QAOpsSettings(provider="gemini", execution_strategy="free_only"),
            _noop_factory,
        )
        order = [p.name for p in ex._providers]
        assert "nvidia" not in order
        # groq (priority 10) leads the free failover, unchanged by this phase.
        assert order[0] == "groq"


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


class TestTransportStillByteIdentical:
    def test_free_only_image_run_transport_reaches_client(
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

        holder: dict = {}

        def _capture(settings: QAOpsSettings) -> _CapturingMock:
            client = _CapturingMock(
                [
                    LLMResponse(text=r, model="m", usage=LLMUsage(input_tokens=1, output_tokens=1))
                    for r in _DOC_RESPONSES
                ]
            )
            holder[settings.provider] = client
            return client

        with patch("qaops.services.design_service.create_client", side_effect=_capture):
            DesignService().run(
                ws / "input" / "ticket.md",
                QAOpsSettings(
                    provider="nvidia",
                    execution_strategy="free_only",
                    output_dir=ws / "output",
                ),
            )

        # Phase 40B: under free_only, the analyzer runs on nvidia (image-capable,
        # free) and receives the image byte-identical; downstream runs on text.
        assert "nvidia" in holder
        client = holder["nvidia"]
        assert client.first_request is not None
        images = client.first_request.messages[0].images
        assert len(images) == 1
        assert base64.b64decode(images[0].data) == png  # byte-identical under free_only
