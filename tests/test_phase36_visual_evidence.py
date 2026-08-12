"""Phase 36 Part 1 tests: EvidencePackage + ImagePart + LLMMessage.images seam.

Part 1 is plumbing only - no provider consumes images yet. These tests prove the
additive image transport, the analyzer-only attachment seam, deterministic Mock
recording of image-bearing requests, the text-only hard-fail, and byte-identical
backward compatibility for text-only requests. Downstream stages must receive no
images.
"""

import contextlib
import json

import pytest
from pydantic import BaseModel, ValidationError

from qaops.core.errors import LLMError
from qaops.ingestion.evidence import EvidencePackage
from qaops.llm import ImagePart, LLMMessage, LLMRequest, MockLLMClient
from qaops.llm.models import LLMResponse, LLMUsage
from qaops.llm.structured import generate_structured


def _img(name: str = "a.png", order: int = 0, media: str = "image/png") -> ImagePart:
    return ImagePart(media_type=media, data="aGVsbG8=", source_filename=name, order=order)


class _Out(BaseModel):
    ok: bool


def _response(payload: str) -> LLMResponse:
    return LLMResponse(
        text=payload, model="mock-model", usage=LLMUsage(input_tokens=1, output_tokens=1)
    )


# -- ImagePart validation -----------------------------------------------------


class TestImagePartValidation:
    def test_valid_png(self) -> None:
        p = _img()
        assert p.media_type == "image/png"
        assert p.order == 0

    def test_valid_jpeg(self) -> None:
        assert _img(media="image/jpeg").media_type == "image/jpeg"

    def test_unsupported_media_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ImagePart(media_type="image/gif", data="x", source_filename="a.gif", order=0)

    def test_empty_data_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ImagePart(media_type="image/png", data="", source_filename="a.png", order=0)

    def test_negative_order_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ImagePart(media_type="image/png", data="x", source_filename="a.png", order=-1)

    def test_optional_provenance_defaults_none(self) -> None:
        p = _img()
        assert p.page is None
        assert p.image_index is None


# -- EvidencePackage construction + ordering ----------------------------------


class TestEvidencePackage:
    def test_default_is_empty(self) -> None:
        pkg = EvidencePackage()
        assert pkg.images == []
        assert pkg.has_images is False

    def test_has_images_true_when_present(self) -> None:
        assert EvidencePackage(images=[_img()]).has_images is True

    def test_ordered_images_by_order_then_filename(self) -> None:
        pkg = EvidencePackage(
            images=[_img("b.png", order=1), _img("a.jpg", order=0, media="image/jpeg")]
        )
        assert [p.source_filename for p in pkg.ordered_images()] == ["a.jpg", "b.png"]

    def test_ordered_images_stable_for_same_order(self) -> None:
        pkg = EvidencePackage(images=[_img("z.png", order=0), _img("a.png", order=0)])
        assert [p.source_filename for p in pkg.ordered_images()] == ["a.png", "z.png"]


# -- LLMMessage.images additive + backward compatible -------------------------


class TestLLMMessageImages:
    def test_text_only_message_has_empty_images(self) -> None:
        assert LLMMessage(role="user", content="hi").images == []

    def test_text_only_serialization_byte_identical(self) -> None:
        # Under exclude_defaults, a text-only message has no "images" key at all,
        # so pre-Phase-36 serialization is unchanged.
        dumped = json.loads(
            LLMMessage(role="user", content="hi").model_dump_json(exclude_defaults=True)
        )
        assert "images" not in dumped
        assert dumped == {"role": "user", "content": "hi"}

    def test_message_carries_images_when_given(self) -> None:
        m = LLMMessage(role="user", content="hi", images=[_img(), _img("b.png", order=1)])
        assert len(m.images) == 2
        assert m.images[0].source_filename == "a.png"


# -- Text-only provider + images -> hard fail (never silent drop) -------------


class TestHardFailOnImages:
    def test_images_with_text_only_provider_raises(self) -> None:
        mock = MockLLMClient([])  # no supports_images -> text-only
        request = LLMRequest(messages=[LLMMessage(role="user", content="hi", images=[_img()])])
        with pytest.raises(LLMError) as exc:
            generate_structured(mock, request, _Out)
        assert "image" in str(exc.value).lower()
        # The provider was never called (fail-fast before completion).
        assert mock.call_count == 0

    def test_text_only_request_still_succeeds(self) -> None:
        mock = MockLLMClient([_response('{"ok": true}')])
        request = LLMRequest(messages=[LLMMessage(role="user", content="hi")])
        result = generate_structured(mock, request, _Out)
        assert result.ok is True


# -- MockLLMClient records image-bearing requests deterministically -----------


class TestMockRecordsImages:
    def test_mock_records_request_with_images(self) -> None:
        # A provider that "supports" images (duck-typed) lets us confirm Mock stores
        # the image-bearing request for assertions - deterministic, no live model.
        class _MultimodalMock(MockLLMClient):
            @property
            def supports_images(self) -> bool:
                return True

        mock = _MultimodalMock([_response('{"ok": true}')])
        request = LLMRequest(
            messages=[
                LLMMessage(role="user", content="hi", images=[_img(), _img("b.png", order=1)])
            ]
        )
        generate_structured(mock, request, _Out)
        assert mock.call_count == 1
        recorded = mock.requests[0]
        assert [p.source_filename for p in recorded.messages[0].images] == ["a.png", "b.png"]


# -- Analyzer plumbing: images reach ONLY the analyzer ------------------------


class _MultimodalMock(MockLLMClient):
    """A MockLLMClient that declares image support, for deterministic seam tests."""

    @property
    def supports_images(self) -> bool:
        return True


class TestAnalyzerPlumbing:
    def _settings(self, tmp_path: object) -> object:
        from qaops.config import QAOpsSettings

        return QAOpsSettings(output_dir=tmp_path / "out")  # type: ignore[operator]

    def test_analyzer_attaches_images_to_its_request(self, tmp_path: object) -> None:
        from qaops.llm import PromptLoader
        from qaops.models import RequirementInput
        from qaops.pipelines.test_design.analyzer import RequirementAnalyzer
        from tests.test_pipeline_test_cases import ANALYZER_RESPONSE

        mock = _MultimodalMock([_response(ANALYZER_RESPONSE)])
        analyzer = RequirementAnalyzer(mock, PromptLoader(), self._settings(tmp_path))
        pkg = EvidencePackage(images=[_img("design.png", order=0)])
        analyzer.run(RequirementInput(text="Some requirements.", source_name="t.md"), pkg)
        assert mock.call_count == 1
        assert [p.source_filename for p in mock.requests[0].messages[0].images] == ["design.png"]

    def test_analyzer_text_only_carries_no_images(self, tmp_path: object) -> None:
        from qaops.llm import PromptLoader
        from qaops.models import RequirementInput
        from qaops.pipelines.test_design.analyzer import RequirementAnalyzer
        from tests.test_pipeline_test_cases import ANALYZER_RESPONSE

        mock = MockLLMClient([_response(ANALYZER_RESPONSE)])
        analyzer = RequirementAnalyzer(mock, PromptLoader(), self._settings(tmp_path))
        # No evidence argument -> backward-compatible call, no images.
        analyzer.run(RequirementInput(text="Some requirements.", source_name="t.md"))
        assert mock.requests[0].messages[0].images == []

    def test_downstream_stage_receives_no_images(self, tmp_path: object) -> None:
        # The business-rule extractor (a downstream stage) must never carry images,
        # even in the same run - only the analyzer does. We assert on the request the
        # stage sent, independent of any post-processing of the response.
        from qaops.llm import PromptLoader
        from qaops.models import Requirement, RequirementAnalysisResult
        from qaops.pipelines.test_design.rules import BusinessRuleExtractor
        from tests.test_pipeline_test_cases import RULES_RESPONSE

        mock = MockLLMClient([_response(RULES_RESPONSE)])
        extractor = BusinessRuleExtractor(mock, PromptLoader(), self._settings(tmp_path))
        analysis = RequirementAnalysisResult(
            source_name="t.md",
            source_text="User logs in.",
            requirements=[Requirement(id="REQ-001", title="Login", description="User logs in.")],
        )
        with contextlib.suppress(Exception):
            extractor.run(analysis)  # business-logic outcome irrelevant; inspect the request
        assert mock.requests, "the stage should have issued a request"
        assert mock.requests[0].messages[0].images == []
