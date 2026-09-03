"""OpenRouter discovery image-capability detection.

discover_openrouter_models now reads architecture.input_modalities to mark a
model image-capable (e.g. nvidia/nemotron-nano-12b-v2-vl:free), so a genuinely
multimodal free model becomes eligible for image stages under the existing
capability filter. Text-only, audio, and metadata-less models must NOT be marked
image-capable. No live HTTP - urlopen is patched.
"""

import io
import json
from unittest.mock import patch

from qaops.execution import discover_openrouter_models
from qaops.execution.selector import StageRequirements, _passes_filter


class _FakeResponse(io.BytesIO):
    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _resp(payload: object) -> _FakeResponse:
    return _FakeResponse(json.dumps(payload).encode("utf-8"))


_PAYLOAD = {
    "data": [
        {
            "id": "nvidia/nemotron-nano-12b-v2-vl:free",
            "context_length": 128000,
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {
                "input_modalities": ["text", "image"],
                "output_modalities": ["text"],
            },
        },
        {
            "id": "google/gemma-4-31b-it:free",
            "context_length": 64000,
            "pricing": {"prompt": "0"},
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        },
        {
            "id": "some/audio-model:free",
            "pricing": {"prompt": "0"},
            "architecture": {"input_modalities": ["audio"], "output_modalities": ["audio"]},
        },
        {
            # No architecture metadata -> image must default False (conservative).
            "id": "legacy/no-arch:free",
            "pricing": {"prompt": "0"},
        },
    ]
}


def _discover():
    with patch("urllib.request.urlopen", return_value=_resp(_PAYLOAD)):
        return {m.name: m for m in discover_openrouter_models()}


class TestImageCapabilityDetection:
    def test_vision_model_marked_image_capable(self) -> None:
        m = _discover()["nvidia/nemotron-nano-12b-v2-vl:free"]
        assert m.images_supported is True
        assert m.text_capable is True
        assert m.free is True

    def test_text_only_model_not_image_capable(self) -> None:
        m = _discover()["google/gemma-4-31b-it:free"]
        assert m.images_supported is False
        assert m.text_capable is True

    def test_audio_model_not_image_or_text(self) -> None:
        m = _discover()["some/audio-model:free"]
        assert m.images_supported is False
        assert m.text_capable is False

    def test_missing_architecture_defaults_image_false(self) -> None:
        # Conservative: without modality metadata we must NOT assume image support,
        # otherwise images could be routed to a text-only model.
        m = _discover()["legacy/no-arch:free"]
        assert m.images_supported is False
        assert m.text_capable is True  # text still defaults True (unchanged)


class TestEligibilityDownstream:
    def test_discovered_vision_model_passes_image_stage_filter(self) -> None:
        m = _discover()["nvidia/nemotron-nano-12b-v2-vl:free"]
        ok, _ = _passes_filter(
            m, StageRequirements(needs_structured_output=True, needs_images=True), set()
        )
        assert ok is True

    def test_discovered_text_model_rejected_by_image_stage(self) -> None:
        m = _discover()["google/gemma-4-31b-it:free"]
        ok, reason = _passes_filter(
            m, StageRequirements(needs_structured_output=True, needs_images=True), set()
        )
        assert ok is False
        assert "image" in reason
