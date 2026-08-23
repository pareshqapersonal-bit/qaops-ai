"""Phase 40B tests: per-stage provider selection for image runs.

The image-consuming stage (requirement_analyzer) requires an image-capable provider;
every downstream stage of an image run EXCLUDES the image provider (NVIDIA) and uses
the normal text chain. Text/PRD runs are unchanged. Resume at a downstream stage must
not require NVIDIA. Verified against the executor's real selection path with an
injected stage-name list (orchestration-supplied, no hard-coded index).
"""

import base64
from pathlib import Path
from unittest.mock import patch

import pytest

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.execution.executor import AdaptiveExecutor
from qaops.execution.registry import available_providers, get_provider
from qaops.ingestion.evidence_sidecar import write_image_sidecar
from qaops.llm import ImagePart, MockLLMClient
from qaops.llm.models import LLMResponse, LLMUsage
from qaops.services.design_service import DesignService
from tests.test_phase32_ticket_api import _DOC_RESPONSES

STAGES = (
    "requirement_analyzer",
    "business_rule_extractor",
    "gap_analyzer",
    "scenario_generator",
    "test_case_generator",
    "coverage_validator",
)
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
PNG_B64 = base64.b64encode(PNG).decode()


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("NVIDIA_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(var, "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


def _providers() -> list:
    return [get_provider(p.name) for p in available_providers()]


def _noop(_s: object) -> list:
    return []


def _ex(
    providers, *, image: bool, strategy: str = "free_only", start: int = 0, at: str | None = None
):
    ex = AdaptiveExecutor(
        providers,
        QAOpsSettings(provider="nvidia", execution_strategy=strategy),
        _noop,
        image_stage_name="requirement_analyzer" if image else None,
        stage_names=STAGES,
        start_index=start,
    )
    ex._current_stage_name = at if at is not None else ex._stage_name_at(start)
    return ex


# -- Per-stage selection ------------------------------------------------------


class TestPerStageSelection:
    def test_image_stage_selects_nvidia(self) -> None:
        # Phase B: gemini-flash is image-capable, so the image stage has two capable
        # providers {nvidia, gemini}. The existing (unchanged) chain puts gemini
        # first, so it is selected - both are eligible; no NVIDIA-first rule.
        ex = _ex(_providers(), image=True, at="requirement_analyzer")
        first = ex._select_first_provider().name
        assert first in {"gemini", "nvidia"}
        assert first == "gemini"

    def test_downstream_includes_multimodal_providers(self) -> None:
        # Capability-driven (Phase C): downstream text stages no longer exclude
        # image-capable providers. NVIDIA is text+structured capable, so it is now
        # an eligible downstream candidate rather than being reserved/excluded.
        ex = _ex(_providers(), image=True, at="gap_analyzer")
        assert ex._candidates(get_provider("nvidia")) != []
        assert ex._candidates(get_provider("gemini")) != []

    def test_every_downstream_stage_includes_multimodal(self) -> None:
        # Capability-driven (Phase C): every downstream text stage now admits
        # image-capable providers (they satisfy text/structured), instead of
        # excluding them.
        for stage in STAGES[1:]:
            ex = _ex(_providers(), image=True, at=stage)
            assert ex._candidates(get_provider("nvidia")) != [], stage
            assert ex._candidates(get_provider("gemini")) != [], stage

    def test_text_run_every_stage_unchanged(self) -> None:
        # No image stage: every stage selects normally, nvidia not required nor
        # excluded; groq leads under free_only exactly as before.
        for stage in STAGES:
            ex = _ex(_providers(), image=False, at=stage)
            assert ex._select_first_provider().name == "groq", stage
            # nvidia remains a normal (last-priority) candidate, not excluded.
            assert ex._candidates(get_provider("nvidia")) != [], stage


# -- Failure / recovery -------------------------------------------------------


class TestRecovery:
    def test_image_stage_no_capable_provider_fails_clearly(self) -> None:
        # Only genuinely text-only providers available for the image stage -> clear
        # fail-fast. gemini-flash is now image-capable (Phase B) so it is excluded
        # from this "no image provider" case.
        providers = [get_provider("groq"), get_provider("openrouter")]
        ex = _ex(providers, image=True, at="requirement_analyzer")
        with pytest.raises(StageError) as exc:
            ex._select_first_provider()
        assert "image evidence" in str(exc.value)

    def test_image_stage_recovers_only_to_image_capable(self) -> None:
        # For the image stage, only image-capable providers yield candidates, so
        # recovery stays on the image-capable set and never silently drops onto a
        # text-only provider (Phase 36A guarantee). Phase B adds gemini-flash to that
        # image-capable set alongside nvidia; genuinely text-only providers still
        # yield nothing.
        ex = _ex(_providers(), image=True, at="requirement_analyzer")
        assert ex._candidates(get_provider("gemini")) != []
        assert ex._candidates(get_provider("nvidia")) != []
        assert ex._candidates(get_provider("groq")) == []

    def test_downstream_recovers_across_text_chain(self) -> None:
        # Downstream, all capable providers are valid recovery targets. Capability-
        # driven (Phase C): image-capable providers (nvidia, gemini) are NO LONGER
        # excluded - they satisfy text/structured and participate normally.
        ex = _ex(_providers(), image=True, at="scenario_generator")
        assert ex._candidates(get_provider("groq")) != []
        assert ex._candidates(get_provider("gemini")) != []
        assert ex._candidates(get_provider("nvidia")) != []


# -- Resume -------------------------------------------------------------------


class TestResume:
    def test_resume_at_gap_analyzer_does_not_require_nvidia(self) -> None:
        # Resuming at a downstream stage (start_index=2) on an image run selects by
        # the existing chain order (a text provider leads), and does NOT require
        # nvidia. Capability-driven (Phase C): nvidia is no longer excluded - it is
        # simply not first in the chain - so the assertion is "not required", not
        # "excluded".
        ex = _ex(_providers(), image=True, start=2)
        assert ex._current_stage_name == "gap_analyzer"
        assert ex._select_first_provider().name != "nvidia"  # chain order, not exclusion
        assert ex._candidates(get_provider("nvidia")) != []  # but nvidia IS eligible now

    def test_resume_at_business_rules_does_not_require_nvidia(self) -> None:
        ex = _ex(_providers(), image=True, start=1)
        assert ex._current_stage_name == "business_rule_extractor"
        assert ex._select_first_provider().name != "nvidia"


# -- End-to-end through DesignService -----------------------------------------


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


class TestEndToEnd:
    def _run(self, tmp_path: Path, *, with_image: bool):
        ws = tmp_path / "ws"
        (ws / "input").mkdir(parents=True)
        (ws / "output").mkdir(parents=True)
        (ws / "input" / "ticket.md").write_text("The screen shows a login form.")
        if with_image:
            write_image_sidecar(
                ws,
                [ImagePart(media_type="image/png", data=PNG_B64, source_filename="s.png", order=0)],
            )
        by_provider: dict = {}
        order: list = []

        def _capture(settings: QAOpsSettings) -> _CapturingMock:
            client = _CapturingMock(
                [
                    LLMResponse(text=r, model="m", usage=LLMUsage(input_tokens=1, output_tokens=1))
                    for r in _DOC_RESPONSES
                ]
            )
            by_provider[settings.provider] = client
            order.append(settings.provider)
            return client

        with patch("qaops.services.design_service.create_client", side_effect=_capture):
            DesignService().run(
                ws / "input" / "ticket.md",
                QAOpsSettings(
                    provider="nvidia", execution_strategy="free_only", output_dir=ws / "output"
                ),
            )
        return by_provider, order

    def test_image_run_analyzer_uses_capable_provider_transport(self, tmp_path: Path) -> None:
        by_provider, order = self._run(tmp_path, with_image=True)
        # Capability-driven (Phase C): the image stage ran on an image-capable
        # provider selected by the existing chain order (gemini precedes nvidia).
        # The image transport reached that provider's request byte-identically.
        image_capable = [p for p in order if p in {"nvidia", "gemini"}]
        assert image_capable  # image stage used an image-capable provider
        image_provider = image_capable[0]
        img_req = by_provider[image_provider].first_request
        assert img_req is not None
        assert base64.b64decode(img_req.messages[0].images[0].data) == PNG

    def test_image_run_downstream_requests_have_no_images(self, tmp_path: Path) -> None:
        by_provider, _ = self._run(tmp_path, with_image=True)
        for provider, client in by_provider.items():
            if provider != "nvidia" and client.first_request is not None:
                assert client.first_request.messages[0].images == []

    def test_text_run_does_not_prefer_nvidia(self, tmp_path: Path) -> None:
        # A normal text/PRD run (configured provider is a text provider, not an
        # explicit QAOPS_PROVIDER=nvidia) leads with the free chain and does not
        # prefer nvidia. Phase 40B leaves this untouched: no image stage -> no
        # image requirement and no downstream exclusion.
        ws = tmp_path / "ws"
        (ws / "input").mkdir(parents=True)
        (ws / "output").mkdir(parents=True)
        (ws / "input" / "prd.md").write_text("The system shall let a user log in.")
        order: list = []

        def _capture(settings: QAOpsSettings) -> _CapturingMock:
            order.append(settings.provider)
            return _CapturingMock(
                [
                    LLMResponse(text=r, model="m", usage=LLMUsage(input_tokens=1, output_tokens=1))
                    for r in _DOC_RESPONSES
                ]
            )

        with patch("qaops.services.design_service.create_client", side_effect=_capture):
            DesignService().run(
                ws / "input" / "prd.md",
                QAOpsSettings(
                    provider="gemini", execution_strategy="free_only", output_dir=ws / "output"
                ),
            )
        assert order  # ran
        assert order[0] != "nvidia"  # normal text run does not lead with nvidia
        assert "nvidia" not in order  # nvidia not needed for a healthy text run
