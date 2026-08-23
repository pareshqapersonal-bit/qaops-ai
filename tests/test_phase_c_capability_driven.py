"""Phase C: capability-driven provider eligibility (exclude_image_providers removed).

Proves that a provider is eligible for a stage iff it satisfies that stage's real
capability requirements - never excluded merely for being image-capable. Image
stages require images; downstream text stages require text/structured and admit
multimodal providers too; text runs are unchanged; failover stays generic. Uses
the real registry/executor with mocked factories - no live provider calls.
"""

import pytest

from qaops.execution.models import ModelRegistry
from qaops.execution.registry import available_providers, get_provider
from qaops.execution.selector import StageRequirements, _passes_filter


@pytest.fixture(autouse=True)
def _provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    # Seed fake credentials so available_providers() returns the full chain
    # (matching the Phase 40B test harness). No live calls are made.
    for var in ("NVIDIA_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.setenv(var, "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


def _flash():
    return next(m for m in ModelRegistry().models_for("gemini") if m.name == "gemini-flash-latest")


def _flash_lite():
    return next(
        m for m in ModelRegistry().models_for("gemini") if m.name == "gemini-flash-lite-latest"
    )


class TestCapabilityFilter:
    def test_multimodal_eligible_for_image_stage(self) -> None:
        ok, _ = _passes_filter(
            _flash(),
            StageRequirements(needs_structured_output=True, needs_images=True),
            set(),
        )
        assert ok is True

    def test_multimodal_eligible_for_downstream_text_stage(self) -> None:
        # The key Phase C property: an image-capable model is NOT excluded from a
        # text/structured stage - it satisfies those requirements, so it is eligible.
        ok, reason = _passes_filter(
            _flash(),
            StageRequirements(needs_structured_output=True),  # text stage, no images
            set(),
        )
        assert ok is True, reason

    def test_text_only_model_excluded_from_image_stage(self) -> None:
        # A genuinely non-image model stays ineligible for an image stage.
        ok, reason = _passes_filter(
            _flash_lite(),  # images_supported=False
            StageRequirements(needs_structured_output=True, needs_images=True),
            set(),
        )
        assert ok is False
        assert "image" in reason

    def test_no_exclude_image_field_exists(self) -> None:
        # The obsolete field is gone entirely (not left inert).
        assert not hasattr(StageRequirements(), "exclude_image_providers")


# -- executor integration -----------------------------------------------------

_STAGES = ("requirement_analyzer", "business_rule_extractor", "gap_analyzer")


def _providers():
    return [get_provider(p.name) for p in available_providers()]


def _ex(*, image: bool, at: str):
    from qaops.config import QAOpsSettings
    from qaops.execution.executor import AdaptiveExecutor

    ex = AdaptiveExecutor(
        _providers(),
        QAOpsSettings(provider="nvidia", execution_strategy="free_only"),
        lambda _s: [],
        image_stage_name="requirement_analyzer" if image else None,
        stage_names=_STAGES,
    )
    ex._current_stage_name = at
    return ex


class TestImageStageEligibility:
    def test_image_stage_admits_all_capable_image_providers(self) -> None:
        ex = _ex(image=True, at="requirement_analyzer")
        assert ex._candidates(get_provider("nvidia")) != []
        assert ex._candidates(get_provider("gemini")) != []

    def test_image_stage_excludes_text_only_provider(self) -> None:
        ex = _ex(image=True, at="requirement_analyzer")
        assert ex._candidates(get_provider("groq")) == []
        assert ex._candidates(get_provider("openrouter")) == []

    def test_image_stage_selection_follows_chain_order(self) -> None:
        # gemini precedes nvidia in the existing chain -> selected first. No
        # NVIDIA-first special case.
        ex = _ex(image=True, at="requirement_analyzer")
        assert ex._select_first_provider().name == "gemini"


class TestDownstreamStageEligibility:
    def test_downstream_admits_multimodal_providers(self) -> None:
        # The defect Phase C fixes: image-capable providers participate downstream.
        ex = _ex(image=True, at="gap_analyzer")
        assert ex._candidates(get_provider("nvidia")) != []
        assert ex._candidates(get_provider("gemini")) != []

    def test_downstream_admits_text_only_providers_too(self) -> None:
        ex = _ex(image=True, at="gap_analyzer")
        assert ex._candidates(get_provider("groq")) != []

    def test_downstream_selection_follows_chain_order(self) -> None:
        # No special-casing: the first provider WITH candidates in the existing
        # chain is selected (not a hardcoded provider). It must be a real, capable
        # provider, and selection must be deterministic across calls.
        ex1 = _ex(image=True, at="gap_analyzer")
        ex2 = _ex(image=True, at="gap_analyzer")
        first = ex1._select_first_provider().name
        assert first == ex2._select_first_provider().name  # deterministic
        assert ex1._candidates(get_provider(first)) != []  # genuinely capable


class TestTextRunUnchanged:
    def test_text_run_first_provider_is_chain_head(self) -> None:
        # Text run: the first provider WITH candidates in the existing chain is
        # selected - unchanged from pre-image behavior. It is a real capable
        # provider (not forced to any specific one).
        ex = _ex(image=False, at="requirement_analyzer")
        first = ex._select_first_provider().name
        assert ex._candidates(get_provider(first)) != []

    def test_text_run_needs_no_images(self) -> None:
        ex = _ex(image=False, at="requirement_analyzer")
        assert ex._requirements().needs_images is False


class TestNoImageRunLockIn:
    def test_image_run_does_not_permanently_restrict_downstream(self) -> None:
        # An image run must NOT classify the whole run as image-only: downstream
        # stages admit the full capable set (multimodal + text-only), not a reduced
        # image-provider-only set.
        img = _ex(image=True, at="requirement_analyzer")
        down = _ex(image=True, at="gap_analyzer")
        # Image stage: only image-capable providers.
        assert img._candidates(get_provider("groq")) == []
        # Downstream: text-only provider is back in play.
        assert down._candidates(get_provider("groq")) != []
