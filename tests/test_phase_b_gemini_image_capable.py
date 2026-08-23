"""Phase B: Gemini Flash model marked image-capable (model-level).

Proves the existing gemini-flash-latest catalogue model now advertises
images_supported=True while keeping its text/structured/free classification, that
no new Gemini candidate was created, that the provider-level images flag stays
False, and that the model is now eligible for image stages. Phase C (removing
exclude_image_providers) has NOT happened, so downstream-stage exclusion behavior
is intentionally left intact and is not asserted here.
"""

from qaops.execution.models import ModelRegistry
from qaops.execution.registry import _REGISTRY
from qaops.execution.selector import StageRequirements, _passes_filter

_FLASH = "gemini-flash-latest"


def _gemini_models():
    return ModelRegistry().models_for("gemini")


def _flash():
    return next(m for m in _gemini_models() if m.name == _FLASH)


class TestFlashCapabilities:
    def test_flash_is_text_and_image_and_structured(self) -> None:
        m = _flash()
        assert m.text_capable is True
        assert m.images_supported is True
        assert m.structured_output is True

    def test_flash_free_classification_unchanged(self) -> None:
        assert _flash().free is True

    def test_flash_priority_unchanged(self) -> None:
        # Priority must not change (no reordering in Phase B).
        assert _flash().priority == 10


class TestEligibility:
    def test_flash_eligible_for_image_stage(self) -> None:
        ok, _ = _passes_filter(
            _flash(),
            StageRequirements(needs_structured_output=True, needs_images=True),
            set(),
        )
        assert ok is True

    def test_flash_still_eligible_for_text_stage(self) -> None:
        ok, _ = _passes_filter(
            _flash(),
            StageRequirements(needs_structured_output=True),
            set(),
        )
        assert ok is True


class TestNoNewCandidateOrProviderChange:
    def test_no_additional_gemini_candidate(self) -> None:
        # Still exactly the three original Gemini models - no vision candidate added.
        names = sorted(m.name for m in _gemini_models())
        assert names == [
            "gemini-flash-latest",
            "gemini-flash-lite-latest",
            "gemini-pro-latest",
        ]

    def test_only_flash_is_image_capable(self) -> None:
        by_name = {m.name: m for m in _gemini_models()}
        assert by_name["gemini-flash-latest"].images_supported is True
        assert by_name["gemini-flash-lite-latest"].images_supported is False
        assert by_name["gemini-pro-latest"].images_supported is False

    def test_provider_level_images_flag_unchanged(self) -> None:
        # Capability is model-level; the provider flag must stay False.
        assert _REGISTRY["gemini"].images is False

    def test_nvidia_provider_flag_unchanged(self) -> None:
        assert _REGISTRY["nvidia"].images is True
