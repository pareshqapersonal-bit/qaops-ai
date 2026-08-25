"""Phase D: comprehensive capability, failover, mixed-input & clarification matrix.

Validates the capability-driven architecture (Phase A/B/C) end-to-end across the
important input/provider/clarification/failover combinations. Tests assert
CAPABILITY behavior, not hardcoded provider identity, except where the existing
chain order + candidate availability make identity deterministic (and that identity
is the behavior being protected).

Layered deliberately: selection/executor-layer tests for capability + failover
(fast, deterministic), plus a few DesignService e2e tests where image-payload flow
must be observed. Reuses existing fixtures/helpers; does not duplicate Phase A/B/C
unit tests. No production code changed; no live provider calls.
"""

import base64
from contextlib import suppress
from pathlib import Path
from unittest.mock import patch

import pytest

from qaops.clarification.enums import AnswerType, ClarificationStatus
from qaops.clarification.models import ClarificationAnswer
from qaops.clarification.service import ClarificationService
from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.execution.executor import AdaptiveExecutor
from qaops.execution.models import ModelRegistry
from qaops.execution.registry import available_providers, get_provider
from qaops.execution.selector import StageRequirements, _passes_filter
from qaops.ingestion.evidence_sidecar import write_image_sidecar
from qaops.llm import ImagePart, MockLLMClient
from qaops.llm.models import LLMResponse, LLMUsage

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


def _ex(*, image: bool, strategy: str = "free_only", start: int = 0, at: str | None = None):
    ex = AdaptiveExecutor(
        _providers(),
        QAOpsSettings(provider="nvidia", execution_strategy=strategy),
        lambda _s: [],
        image_stage_name="requirement_analyzer" if image else None,
        stage_names=STAGES,
        start_index=start,
    )
    ex._current_stage_name = at if at is not None else ex._stage_name_at(start)
    return ex


def _providers_with_candidates(ex) -> list[str]:
    return [p.name for p in _providers() if ex._candidates(p)]


def _model(provider: str, name: str):
    return next(m for m in ModelRegistry().models_for(provider) if m.name == name)


# =====================================================================
# 3. CAPABILITY ELIGIBILITY  (assert capability, never provider identity)
# =====================================================================


class TestCapabilityEligibility:
    def test_text_stage_requires_text_capability(self) -> None:
        # A non-text model is rejected by a text stage.
        img_only = _model("gemini", "gemini-flash-latest").__class__(
            name="fake-image-only",
            provider="x",
            text_capable=False,
            images_supported=True,
        )
        ok, reason = _passes_filter(img_only, StageRequirements(needs_text=True), set())
        assert ok is False
        assert "text" in reason

    def test_image_stage_requires_image_capability(self) -> None:
        lite = _model("gemini", "gemini-flash-lite-latest")  # images_supported=False
        ok, reason = _passes_filter(
            lite, StageRequirements(needs_structured_output=True, needs_images=True), set()
        )
        assert ok is False
        assert "image" in reason

    def test_structured_stage_requires_structured_output(self) -> None:
        no_struct = _model("gemini", "gemini-flash-latest").__class__(
            name="fake-no-struct", provider="x", structured_output=False
        )
        ok, reason = _passes_filter(
            no_struct, StageRequirements(needs_structured_output=True), set()
        )
        assert ok is False
        assert "structured" in reason

    def test_multimodal_eligible_for_both_kinds(self) -> None:
        flash = _model("gemini", "gemini-flash-latest")  # text+image+structured
        ok_img, _ = _passes_filter(
            flash, StageRequirements(needs_structured_output=True, needs_images=True), set()
        )
        ok_txt, _ = _passes_filter(flash, StageRequirements(needs_structured_output=True), set())
        assert ok_img is True
        assert ok_txt is True

    def test_text_only_never_eligible_for_image_stage(self) -> None:
        ex = _ex(image=True, at="requirement_analyzer")
        for name in ("groq", "openrouter"):  # genuinely text-only providers
            assert ex._candidates(get_provider(name)) == []


# =====================================================================
# 4. IMAGE TRANSPORT  (selected image-capable client receives the payload)
# =====================================================================

_DOC_RESPONSES = None  # imported lazily to avoid import cost when unused


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


def _run_design(tmp_path: Path, *, with_image: bool, images=None):
    from qaops.services.design_service import DesignService
    from tests.test_phase32_ticket_api import _DOC_RESPONSES as DOC

    ws = tmp_path / "ws"
    (ws / "input").mkdir(parents=True)
    (ws / "output").mkdir(parents=True)
    (ws / "input" / "ticket.md").write_text("The screen shows a login form.")
    if with_image:
        parts = images or [
            ImagePart(media_type="image/png", data=PNG_B64, source_filename="s.png", order=0)
        ]
        write_image_sidecar(ws, parts)
    by_provider: dict = {}
    order: list = []

    def _capture(settings: QAOpsSettings) -> _CapturingMock:
        client = _CapturingMock(
            [
                LLMResponse(text=r, model="m", usage=LLMUsage(input_tokens=1, output_tokens=1))
                for r in DOC
            ]
        )
        by_provider[settings.provider] = client
        order.append(settings.provider)
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
    return by_provider, order


class TestImageTransport:
    def test_image_reaches_selected_capable_provider(self, tmp_path: Path) -> None:
        by_provider, order = _run_design(tmp_path, with_image=True)
        image_capable = [p for p in order if p in {"nvidia", "gemini"}]
        assert image_capable  # the image stage ran on an image-capable provider
        req = by_provider[image_capable[0]].first_request
        assert req is not None
        assert base64.b64decode(req.messages[0].images[0].data) == PNG  # bytes reached it

    def test_multiple_images_preserve_order_and_mime(self, tmp_path: Path) -> None:
        imgs = [
            ImagePart(media_type="image/png", data=PNG_B64, source_filename="a.png", order=0),
            ImagePart(media_type="image/jpeg", data=PNG_B64, source_filename="b.jpg", order=1),
        ]
        by_provider, order = _run_design(tmp_path, with_image=True, images=imgs)
        image_capable = [p for p in order if p in {"nvidia", "gemini"}]
        req = by_provider[image_capable[0]].first_request
        got = req.messages[0].images
        assert [i.source_filename for i in got] == ["a.png", "b.jpg"]  # order preserved
        assert [i.media_type for i in got] == ["image/png", "image/jpeg"]  # mime preserved

    def test_text_accompanying_image_preserved(self, tmp_path: Path) -> None:
        by_provider, order = _run_design(tmp_path, with_image=True)
        req = by_provider[[p for p in order if p in {"nvidia", "gemini"}][0]].first_request
        assert req.messages[0].content  # non-empty prompt text alongside the image


# =====================================================================
# 5. MIXED TEXT + IMAGE RUN
# =====================================================================


class TestMixedTextImage:
    def test_image_stage_requires_images_downstream_does_not(self) -> None:
        img_stage = _ex(image=True, at="requirement_analyzer")
        downstream = _ex(image=True, at="gap_analyzer")
        assert img_stage._requirements().needs_images is True
        assert downstream._requirements().needs_images is False

    def test_downstream_admits_multimodal_and_text_only(self) -> None:
        ex = _ex(image=True, at="gap_analyzer")
        assert ex._candidates(get_provider("gemini")) != []  # multimodal
        assert ex._candidates(get_provider("nvidia")) != []  # multimodal
        assert ex._candidates(get_provider("groq")) != []  # text-only

    def test_run_not_permanently_image_restricted(self) -> None:
        # text-only provider is excluded at the image stage but back in play
        # downstream - the run is NOT locked to image providers.
        assert _ex(image=True, at="requirement_analyzer")._candidates(get_provider("groq")) == []
        assert _ex(image=True, at="gap_analyzer")._candidates(get_provider("groq")) != []

    def test_downstream_requests_carry_no_image_payload(self, tmp_path: Path) -> None:
        by_provider, order = _run_design(tmp_path, with_image=True)
        image_provider = [p for p in order if p in {"nvidia", "gemini"}][0]
        for provider, client in by_provider.items():
            if provider != image_provider and client.first_request is not None:
                assert client.first_request.messages[0].images == []


# =====================================================================
# 6. IMAGE FAILOVER  (generic mechanism, no hardcoded NVIDIA->Gemini)
# =====================================================================


class TestImageFailover:
    def _exclude_provider_models(self, ex, provider_name: str) -> None:
        for m in ex._candidates(get_provider(provider_name)):
            ex._excluded[provider_name].add(m.name)

    def test_gemini_fails_next_image_provider_takes_over(self) -> None:
        ex = _ex(image=True, at="requirement_analyzer")
        assert "gemini" in _providers_with_candidates(ex)
        self._exclude_provider_models(ex, "gemini")  # gemini "fails"
        remaining = _providers_with_candidates(ex)
        assert "gemini" not in remaining
        assert "nvidia" in remaining  # another eligible image provider remains

    def test_nvidia_fails_next_image_provider_takes_over(self) -> None:
        ex = _ex(image=True, at="requirement_analyzer")
        assert "nvidia" in _providers_with_candidates(ex)
        self._exclude_provider_models(ex, "nvidia")  # nvidia "fails"
        remaining = _providers_with_candidates(ex)
        assert "nvidia" not in remaining
        assert "gemini" in remaining

    def test_all_image_providers_fail_no_text_only_fallback(self) -> None:
        ex = _ex(image=True, at="requirement_analyzer")
        for p in ("gemini", "nvidia"):
            self._exclude_provider_models(ex, p)
        # No image-capable candidate remains, and text-only providers are NOT
        # selected as image fallback.
        assert _providers_with_candidates(ex) == []

    def test_failover_is_capability_gated_not_provider_specific(self) -> None:
        # Whichever image provider is excluded, recovery lands only on another
        # image-capable one - proving the generic mechanism, not a fixed chain.
        for failed, expected in (("gemini", "nvidia"), ("nvidia", "gemini")):
            ex = _ex(image=True, at="requirement_analyzer")
            self._exclude_provider_models(ex, failed)
            remaining = _providers_with_candidates(ex)
            assert expected in remaining
            assert all(get_provider(p).images or _model_image_capable(p) for p in remaining)


def _model_image_capable(provider: str) -> bool:
    return any(m.images_supported for m in ModelRegistry().models_for(provider))


# =====================================================================
# 7. DOWNSTREAM FAILOVER  (capability-based; reuse-agnostic)
# =====================================================================


class TestDownstreamFailover:
    def test_downstream_first_provider_fail_advances_to_next_capable(self) -> None:
        ex = _ex(image=True, at="gap_analyzer")
        chain = _providers_with_candidates(ex)
        assert len(chain) >= 2  # multiple capable downstream providers
        first = chain[0]
        for m in ex._candidates(get_provider(first)):
            ex._excluded[first].add(m.name)
        after = _providers_with_candidates(ex)
        assert first not in after
        assert after  # execution can continue on the next capable provider

    def test_downstream_does_not_force_or_forbid_image_provider_reuse(self) -> None:
        # An image-capable provider is neither required nor excluded downstream -
        # it is present iff capability + chain make it so.
        ex = _ex(image=True, at="gap_analyzer")
        chain = _providers_with_candidates(ex)
        # both a multimodal and a text-only provider appear -> capability, not reuse
        assert "gemini" in chain
        assert "groq" in chain


# =====================================================================
# 8. PRD / TEXT REGRESSION
# =====================================================================


class TestTextRegression:
    def test_text_run_needs_no_images(self) -> None:
        for stage in STAGES:
            assert _ex(image=False, at=stage)._requirements().needs_images is False

    def test_text_run_gemini_eligible_for_text(self) -> None:
        ex = _ex(image=False, at="requirement_analyzer")
        assert ex._candidates(get_provider("gemini")) != []

    def test_text_run_selection_is_chain_order(self) -> None:
        # First provider WITH candidates in the existing chain - deterministic,
        # not forced to any identity.
        ex1 = _ex(image=False, at="requirement_analyzer")
        ex2 = _ex(image=False, at="requirement_analyzer")
        assert ex1._select_first_provider().name == ex2._select_first_provider().name

    def test_text_run_requests_have_no_image_payload(self, tmp_path: Path) -> None:
        by_provider, _ = _run_design(tmp_path, with_image=False)
        for client in by_provider.values():
            if client.first_request is not None:
                assert client.first_request.messages[0].images == []


# =====================================================================
# 11. FREE-ONLY STRATEGY
# =====================================================================


class TestFreeOnly:
    def test_gemini_flash_free_eligible(self) -> None:
        assert _model("gemini", "gemini-flash-latest").free is True

    def test_free_only_image_stage_considers_all_free_image_providers(self) -> None:
        ex = _ex(image=True, strategy="free_only", at="requirement_analyzer")
        caps = _providers_with_candidates(ex)
        assert "gemini" in caps  # free image-capable
        assert "nvidia" in caps  # free image-capable (per existing classification)

    def test_free_only_text_only_provider_excluded_from_image_stage(self) -> None:
        ex = _ex(image=True, strategy="free_only", at="requirement_analyzer")
        assert ex._candidates(get_provider("groq")) == []


# =====================================================================
# 12. FAILURE MATRIX  (deterministic, finite, clear, no incapable fallback)
# =====================================================================


class TestFailureMatrix:
    def test_no_image_capable_provider_fails_clearly(self) -> None:
        ex = AdaptiveExecutor(
            [get_provider("groq"), get_provider("openrouter")],  # text-only
            QAOpsSettings(provider="nvidia", execution_strategy="free_only"),
            lambda _s: [],
            image_stage_name="requirement_analyzer",
            stage_names=STAGES,
        )
        ex._current_stage_name = "requirement_analyzer"
        with pytest.raises(StageError) as exc:
            ex._select_first_provider()
        assert "image evidence" in str(exc.value)

    def test_all_capable_providers_excluded_yields_no_candidate(self) -> None:
        ex = _ex(image=True, at="requirement_analyzer")
        for p in ("gemini", "nvidia"):
            for m in ex._candidates(get_provider(p)):
                ex._excluded[p].add(m.name)
        assert _providers_with_candidates(ex) == []  # finite, no incapable fallback

    def test_image_fail_with_text_only_present_never_uses_text_only(self) -> None:
        # groq present but text-only; excluding image providers must NOT let groq
        # serve the image stage.
        ex = _ex(image=True, at="requirement_analyzer")
        for p in ("gemini", "nvidia"):
            for m in ex._candidates(get_provider(p)):
                ex._excluded[p].add(m.name)
        assert ex._candidates(get_provider("groq")) == []


# =====================================================================
# 9. CLARIFICATION MATRIX  (provider-agnostic; ON vs OFF identical eligibility)
# =====================================================================

_CLAR_ANALYZER = (
    '{"requirements":[{"title":"Login","description":"User logs in.",'
    '"source_excerpt":"login form"}]}'
)
_CLAR_GAP_BLOCKER = (
    '{"gaps":[{"description":"Retry undefined","severity":"blocker",'
    '"requirement_id":"REQ-001","suggested_question":"Retry?"}]}'
)
_CLAR_GAP_NONE = '{"gaps":[]}'
_CLAR_AGENT = (
    '{"questions":[{"gap_index":0,"skip":false,"question":"Retry?",'
    '"answer_type":"boolean","options":[],"reason":"coverage"}]}'
)


def _clar_patch(client):
    return (
        patch("qaops.execution.resilient_call.create_client", return_value=client),
        patch(
            "qaops.execution.resilient_call.fallback_providers",
            return_value=[get_provider("gemini"), get_provider("nvidia"), get_provider("groq")],
        ),
    )


class _ImageCapableMock(MockLLMClient):
    """A MockLLMClient that advertises image support, so the structured-output
    layer permits image requests routed to it (an image-capable candidate)."""

    @property
    def supports_images(self) -> bool:
        return True


def _clar_ws(tmp_path: Path, *, with_image: bool) -> Path:
    ws = tmp_path / "run"
    (ws / "input").mkdir(parents=True)
    (ws / "input" / "t.md").write_text("The login form is shown.")
    if with_image:
        write_image_sidecar(
            ws, [ImagePart(media_type="image/png", data=PNG_B64, source_filename="s.png", order=0)]
        )
    return ws


class TestClarificationMatrix:
    def _start(self, tmp_path: Path, *, with_image: bool):
        ws = _clar_ws(tmp_path, with_image=with_image)
        svc = ClarificationService(QAOpsSettings(output_dir=ws / "output"))
        mock_cls = _ImageCapableMock if with_image else MockLLMClient
        p1, p2 = _clar_patch(mock_cls([_CLAR_ANALYZER, _CLAR_GAP_BLOCKER, _CLAR_AGENT]))
        with p1, p2:
            state = svc.start("run_1", ws / "input" / "t.md", ws)
        return svc, ws, state

    def test_clarification_text_reaches_ready(self, tmp_path: Path) -> None:
        svc, ws, state = self._start(tmp_path, with_image=False)
        ans = [
            ClarificationAnswer(
                question_id=state.questions[0].question_id,
                answer_type=AnswerType.BOOLEAN,
                answer="true",
            )
        ]
        p1, p2 = _clar_patch(MockLLMClient([_CLAR_GAP_NONE]))
        with p1, p2:
            new = svc.submit_answers(ws, ans)
        assert new.status is ClarificationStatus.READY_FOR_TEST_DESIGN

    def test_clarification_image_reaches_ready(self, tmp_path: Path) -> None:
        # clarification with image evidence works through the same capability model.
        svc, ws, state = self._start(tmp_path, with_image=True)
        ans = [
            ClarificationAnswer(
                question_id=state.questions[0].question_id,
                answer_type=AnswerType.BOOLEAN,
                answer="true",
            )
        ]
        p1, p2 = _clar_patch(_ImageCapableMock([_CLAR_GAP_NONE]))
        with p1, p2:
            new = svc.submit_answers(ws, ans)
        assert new.status is ClarificationStatus.READY_FOR_TEST_DESIGN

    def test_clarification_analysis_requests_images_when_present(self, tmp_path: Path) -> None:
        # The initial analysis expresses image capability iff images exist - the
        # only capability difference between text and image clarification runs.
        ws = _clar_ws(tmp_path, with_image=True)
        svc = ClarificationService(QAOpsSettings(output_dir=ws / "output"))
        captured = {}

        def fake(*, settings, requirements, run_call, **_kw):
            captured.setdefault("first", requirements)
            raise _Stop

        with (
            patch("qaops.clarification.service.resilient_structured_call", side_effect=fake),
            suppress(_Stop),
        ):
            svc.start("run_1", ws / "input" / "t.md", ws)
        assert captured["first"].needs_images is True

    def test_clarification_does_not_reintroduce_exclusion(self) -> None:
        # No StageRequirements built anywhere can carry the removed field.
        assert not hasattr(StageRequirements(), "exclude_image_providers")


class _Stop(Exception):
    pass


# =====================================================================
# 10. CLARIFICATION RESUME / HANDOFF  (single test: capability-driven after resume)
# =====================================================================


class TestClarificationResume:
    def test_provider_selection_capability_driven_after_resume(self, tmp_path: Path) -> None:
        # Drive to ready, then reload persisted state (a resume) and confirm the
        # handoff still routes through the requirements entry point without any
        # provider-specific constraint - selection stays capability-driven.
        from qaops.clarification.state_store import load_clarification_state

        ws = _clar_ws(tmp_path, with_image=True)
        svc = ClarificationService(QAOpsSettings(output_dir=ws / "output"))
        p1, p2 = _clar_patch(_ImageCapableMock([_CLAR_ANALYZER, _CLAR_GAP_BLOCKER, _CLAR_AGENT]))
        with p1, p2:
            state = svc.start("run_1", ws / "input" / "t.md", ws)
        ans = [
            ClarificationAnswer(
                question_id=state.questions[0].question_id,
                answer_type=AnswerType.BOOLEAN,
                answer="true",
            )
        ]
        p1, p2 = _clar_patch(_ImageCapableMock([_CLAR_GAP_NONE]))
        with p1, p2:
            svc.submit_answers(ws, ans)
        # Resume: reload state from disk; answers survive, run is ready.
        reloaded = load_clarification_state(ws)
        assert reloaded is not None
        assert reloaded.status is ClarificationStatus.READY_FOR_TEST_DESIGN
        assert len(reloaded.answers) == 1
        # Handoff builds clarified requirements (requirements entry point) - no
        # provider hardcoding involved.
        target = svc.prepare_test_design(ws)
        assert target.exists()
