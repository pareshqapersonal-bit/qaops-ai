"""Phase 37 tests: the NVIDIA (Nemotron) OpenAI-compatible provider.

No live NVIDIA calls in the normal suite - an injected MagicMock SDK captures the
outgoing payload (following the OpenRouter test pattern). These prove construction,
factory wiring, supports_images, text-only shape, image content-part conversion
(PNG/JPEG/multiple/order/base64 integrity), error mapping, and structured-output
compatibility. A separate opt-in live smoke test lives at the bottom.
"""

import base64
import os
from unittest.mock import MagicMock

import pytest
from openai import OpenAIError
from pydantic import BaseModel

from qaops.config import QAOpsSettings
from qaops.llm.errors import LLMProviderError
from qaops.llm.factory import create_client
from qaops.llm.models import ImagePart, LLMMessage, LLMRequest
from qaops.llm.nvidia_client import NvidiaClient
from qaops.llm.structured import generate_structured

MODEL = "nvidia/nemotron-nano-12b-v2-vl"
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)
PNG_B64 = base64.b64encode(PNG_BYTES).decode()
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 20
JPEG_B64 = base64.b64encode(JPEG_BYTES).decode()


def _sdk(
    content: str = "OK", *, model: str | None = MODEL, finish: str | None = "stop"
) -> MagicMock:
    sdk = MagicMock()
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content), finish_reason=finish)]
    response.model = model
    response.usage = MagicMock(prompt_tokens=11, completion_tokens=4)
    sdk.chat.completions.create.return_value = response
    return sdk


def _img(
    name: str = "a.png", order: int = 0, media: str = "image/png", data: str = PNG_B64
) -> ImagePart:
    return ImagePart(media_type=media, data=data, source_filename=name, order=order)


def _sent_messages(sdk: MagicMock) -> list[dict]:
    return sdk.chat.completions.create.call_args.kwargs["messages"]


# -- Construction + factory + capability --------------------------------------


class TestConstruction:
    def test_provider_name(self) -> None:
        assert NvidiaClient(model=MODEL, sdk_client=_sdk()).provider_name == "nvidia"

    def test_model(self) -> None:
        assert NvidiaClient(model=MODEL, sdk_client=_sdk()).model == MODEL

    def test_default_model_and_base_url(self) -> None:
        c = NvidiaClient(sdk_client=_sdk())
        assert c.model == "nvidia/nemotron-nano-12b-v2-vl"
        assert c._base_url == "https://integrate.api.nvidia.com/v1"

    def test_supports_images_true(self) -> None:
        assert NvidiaClient(model=MODEL, sdk_client=_sdk()).supports_images is True

    def test_missing_api_key_fails_fast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from qaops.core.errors import ConfigurationError

        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        # No injected SDK -> real construction path resolves the key and must fail.
        with pytest.raises(ConfigurationError, match="NVIDIA_API_KEY"):
            NvidiaClient(model=MODEL)


class TestFactory:
    def test_provider_nvidia_returns_nvidia_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "sk-test")
        settings = QAOpsSettings(provider="nvidia")
        client = create_client(settings)
        assert isinstance(client, NvidiaClient)
        assert client.provider_name == "nvidia"
        assert client.model == "nvidia/nemotron-nano-12b-v2-vl"

    def test_existing_providers_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        client = create_client(QAOpsSettings(provider="anthropic"))
        assert client.provider_name == "anthropic"

    def test_custom_base_url_threaded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NVIDIA_API_KEY", "sk-test")
        settings = QAOpsSettings(provider="nvidia", nvidia_base_url="http://localhost:8000/v1")
        client = create_client(settings)
        assert isinstance(client, NvidiaClient)
        assert client._base_url == "http://localhost:8000/v1"


# -- Text-only request (plain-string content, no image parts) -----------------


class TestTextOnly:
    def test_text_reaches_sdk_as_plain_string(self) -> None:
        sdk = _sdk()
        NvidiaClient(model=MODEL, sdk_client=sdk).complete(
            LLMRequest(messages=[LLMMessage(role="user", content="hello")])
        )
        msgs = _sent_messages(sdk)
        user = [m for m in msgs if m["role"] == "user"][0]
        assert user["content"] == "hello"  # plain string, not a parts array

    def test_system_prompt_prepended(self) -> None:
        sdk = _sdk()
        NvidiaClient(model=MODEL, sdk_client=sdk).complete(
            LLMRequest(system="SYS", messages=[LLMMessage(role="user", content="hi")])
        )
        msgs = _sent_messages(sdk)
        assert msgs[0] == {"role": "system", "content": "SYS"}


# -- Image conversion ---------------------------------------------------------


class TestImageConversion:
    def test_single_png(self) -> None:
        sdk = _sdk()
        NvidiaClient(model=MODEL, sdk_client=sdk).complete(
            LLMRequest(messages=[LLMMessage(role="user", content="look", images=[_img()])])
        )
        content = [m for m in _sent_messages(sdk) if m["role"] == "user"][0]["content"]
        assert content[0] == {"type": "text", "text": "look"}
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"] == f"data:image/png;base64,{PNG_B64}"

    def test_single_jpeg(self) -> None:
        sdk = _sdk()
        NvidiaClient(model=MODEL, sdk_client=sdk).complete(
            LLMRequest(
                messages=[
                    LLMMessage(
                        role="user",
                        content="look",
                        images=[_img(name="a.jpg", media="image/jpeg", data=JPEG_B64)],
                    )
                ]
            )
        )
        content = [m for m in _sent_messages(sdk) if m["role"] == "user"][0]["content"]
        assert content[1]["image_url"]["url"] == f"data:image/jpeg;base64,{JPEG_B64}"

    def test_multiple_images_all_present_in_order(self) -> None:
        sdk = _sdk()
        imgs = [
            _img(name="one.png", order=0),
            _img(name="two.jpg", order=1, media="image/jpeg", data=JPEG_B64),
            _img(name="three.png", order=2),
        ]
        NvidiaClient(model=MODEL, sdk_client=sdk).complete(
            LLMRequest(messages=[LLMMessage(role="user", content="all", images=imgs)])
        )
        content = [m for m in _sent_messages(sdk) if m["role"] == "user"][0]["content"]
        image_parts = [p for p in content if p["type"] == "image_url"]
        assert len(image_parts) == 3
        urls = [p["image_url"]["url"] for p in image_parts]
        assert urls[0].startswith("data:image/png")
        assert urls[1].startswith("data:image/jpeg")
        assert urls[2].startswith("data:image/png")

    def test_mixed_text_and_images(self) -> None:
        sdk = _sdk()
        NvidiaClient(model=MODEL, sdk_client=sdk).complete(
            LLMRequest(
                messages=[
                    LLMMessage(
                        role="user",
                        content="describe",
                        images=[_img(), _img(name="b.png", order=1)],
                    )
                ]
            )
        )
        content = [m for m in _sent_messages(sdk) if m["role"] == "user"][0]["content"]
        assert content[0] == {"type": "text", "text": "describe"}
        assert sum(1 for p in content if p["type"] == "image_url") == 2

    def test_base64_integrity_roundtrip(self) -> None:
        sdk = _sdk()
        NvidiaClient(model=MODEL, sdk_client=sdk).complete(
            LLMRequest(messages=[LLMMessage(role="user", content="x", images=[_img()])])
        )
        content = [m for m in _sent_messages(sdk) if m["role"] == "user"][0]["content"]
        uri = content[1]["image_url"]["url"]
        b64 = uri.split(",", 1)[1]
        assert b64 == PNG_B64
        assert base64.b64decode(b64) == PNG_BYTES  # byte-identical to the original

    def test_source_filename_not_in_uri(self) -> None:
        sdk = _sdk()
        NvidiaClient(model=MODEL, sdk_client=sdk).complete(
            LLMRequest(
                messages=[LLMMessage(role="user", content="x", images=[_img(name="secret.png")])]
            )
        )
        content = [m for m in _sent_messages(sdk) if m["role"] == "user"][0]["content"]
        assert "secret.png" not in content[1]["image_url"]["url"]

    def test_empty_images_behaves_text_only(self) -> None:
        sdk = _sdk()
        NvidiaClient(model=MODEL, sdk_client=sdk).complete(
            LLMRequest(messages=[LLMMessage(role="user", content="hi", images=[])])
        )
        content = [m for m in _sent_messages(sdk) if m["role"] == "user"][0]["content"]
        assert content == "hi"  # plain string


# -- Error handling (reuse existing LLMProviderError) -------------------------


class TestErrors:
    def test_sdk_error_becomes_llm_provider_error(self) -> None:
        sdk = MagicMock()
        sdk.chat.completions.create.side_effect = OpenAIError("boom")
        with pytest.raises(LLMProviderError):
            NvidiaClient(model=MODEL, sdk_client=sdk).complete(
                LLMRequest(messages=[LLMMessage(role="user", content="x")])
            )

    def test_empty_response_content(self) -> None:
        client = NvidiaClient(model=MODEL, sdk_client=_sdk(content=""))
        result = client.complete(LLMRequest(messages=[LLMMessage(role="user", content="x")]))
        assert result.text == ""

    def test_null_model_falls_back_to_configured(self) -> None:
        client = NvidiaClient(model=MODEL, sdk_client=_sdk(model=None))
        result = client.complete(LLMRequest(messages=[LLMMessage(role="user", content="x")]))
        assert result.model == MODEL


# -- Structured-output compatibility (provider-agnostic loop) -----------------


class _Schema(BaseModel):
    ok: bool


class TestStructuredOutput:
    def test_structured_output_parses_textual_response(self) -> None:
        client = NvidiaClient(model=MODEL, sdk_client=_sdk(content='{"ok": true}'))
        result = generate_structured(
            client, LLMRequest(messages=[LLMMessage(role="user", content="x")]), _Schema
        )
        assert result.ok is True

    def test_image_plus_structured_request(self) -> None:
        # supports_images=True lets the image request through; a valid textual JSON
        # response is parsed by the existing structured-output loop.
        client = NvidiaClient(model=MODEL, sdk_client=_sdk(content='{"ok": true}'))
        result = generate_structured(
            client,
            LLMRequest(messages=[LLMMessage(role="user", content="x", images=[_img()])]),
            _Schema,
        )
        assert result.ok is True


# -- Live smoke test (opt-in, never in normal suite) --------------------------


@pytest.mark.skipif(
    os.environ.get("QAOPS_LIVE_NVIDIA") != "1" or not os.environ.get("NVIDIA_API_KEY"),
    reason="Live NVIDIA test requires QAOPS_LIVE_NVIDIA=1 and NVIDIA_API_KEY.",
)
def test_live_nvidia_smoke() -> None:
    client = NvidiaClient()  # real SDK, real endpoint
    request = LLMRequest(
        messages=[
            LLMMessage(
                role="user",
                content="What is in this image? Answer briefly.",
                images=[_img(name="pixel.png")],
            )
        ]
    )
    response = client.complete(request)
    assert response.text.strip()  # non-empty textual response

    structured = generate_structured(
        client,
        LLMRequest(
            messages=[
                LLMMessage(
                    role="user",
                    content='Return JSON {"ok": true} describing whether you can see the image.',
                    images=[_img(name="pixel.png")],
                )
            ]
        ),
        _Schema,
    )
    assert isinstance(structured, _Schema)
