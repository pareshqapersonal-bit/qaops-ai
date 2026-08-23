"""Phase A: Gemini client image-support tests.

Proves the Gemini client's new image branch: text-only requests are byte-for-byte
unchanged, image messages become Gemini inline-data parts via Part.from_bytes with
exact base64->bytes decoding, MIME preserved, order preserved, and supports_images
is exposed. The SDK client is a fake that records the contents it receives - no live
provider calls. ImagePart/EvidencePackage/LLMMessage are only read, never modified.
"""

import base64

from qaops.llm.gemini_client import GeminiClient, _message_parts
from qaops.llm.models import ImagePart, LLMMessage, LLMRequest


def _img(data: bytes, media_type: str = "image/png", order: int = 0) -> ImagePart:
    return ImagePart(
        media_type=media_type,
        data=base64.b64encode(data).decode(),
        source_filename=f"img{order}.png",
        order=order,
    )


class _FakeResponse:
    text = "ok"
    usage_metadata = None
    candidates: list = []


class _FakeModels:
    def __init__(self) -> None:
        self.last_contents = None

    def generate_content(self, *, model, contents, config):  # noqa: ANN001
        self.last_contents = contents
        return _FakeResponse()


class _FakeSDK:
    def __init__(self) -> None:
        self.models = _FakeModels()


class TestTextUnchanged:
    def test_text_only_single_part(self) -> None:
        parts = _message_parts(LLMMessage(role="user", content="hello world"))
        assert len(parts) == 1
        assert parts[0].text == "hello world"

    def test_complete_text_sends_one_text_part(self) -> None:
        sdk = _FakeSDK()
        c = GeminiClient(model="gemini-flash-latest", sdk_client=sdk)
        c.complete(LLMRequest(messages=[LLMMessage(role="user", content="hi")]))
        parts = sdk.models.last_contents[0].parts
        assert len(parts) == 1
        assert parts[0].text == "hi"


class TestImageTransport:
    def test_image_message_text_first_then_image(self) -> None:
        parts = _message_parts(
            LLMMessage(role="user", content="describe", images=[_img(b"PNGDATA")])
        )
        assert len(parts) == 2
        assert parts[0].text == "describe"
        assert parts[1].inline_data is not None

    def test_base64_decoded_exactly(self) -> None:
        raw = bytes(range(256))  # every byte value
        parts = _message_parts(LLMMessage(role="user", content="x", images=[_img(raw)]))
        assert parts[1].inline_data.data == raw

    def test_mime_type_preserved(self) -> None:
        for mt in ("image/png", "image/jpeg"):
            parts = _message_parts(
                LLMMessage(role="user", content="x", images=[_img(b"D", media_type=mt)])
            )
            assert parts[1].inline_data.mime_type == mt

    def test_multiple_images_order_preserved(self) -> None:
        imgs = [_img(b"first", order=0), _img(b"second", order=1), _img(b"third", order=2)]
        parts = _message_parts(LLMMessage(role="user", content="x", images=imgs))
        assert parts[0].text == "x"
        assert parts[1].inline_data.data == b"first"
        assert parts[2].inline_data.data == b"second"
        assert parts[3].inline_data.data == b"third"

    def test_complete_image_sends_text_plus_image(self) -> None:
        sdk = _FakeSDK()
        c = GeminiClient(model="gemini-flash-latest", sdk_client=sdk)
        msg = LLMMessage(role="user", content="see", images=[_img(b"IMG")])
        c.complete(LLMRequest(messages=[msg]))
        parts = sdk.models.last_contents[0].parts
        assert len(parts) == 2
        assert parts[0].text == "see"
        assert parts[1].inline_data.data == b"IMG"


class TestSupportsImages:
    def test_client_supports_images(self) -> None:
        c = GeminiClient(model="gemini-flash-latest", sdk_client=_FakeSDK())
        assert c.supports_images is True
