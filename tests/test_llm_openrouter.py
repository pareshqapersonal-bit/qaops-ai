"""Offline tests for OpenRouterClient.

Mirrors test_llm_gemini.py: request/response translation, error wrapping,
and fail-fast on a missing key. The SDK is always mocked - no network.
"""

from unittest.mock import MagicMock

import pytest
from openai import OpenAIError

from qaops.core.errors import ConfigurationError
from qaops.llm.client import LLMClient
from qaops.llm.errors import LLMProviderError
from qaops.llm.models import LLMMessage, LLMRequest
from qaops.llm.openrouter_client import OpenRouterClient

MODEL = "openai/gpt-oss-20b:free"


def _sdk(
    content: str = "{}", *, model: str | None = MODEL, finish: str | None = "stop"
) -> MagicMock:
    sdk = MagicMock()
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content), finish_reason=finish)]
    response.model = model
    response.usage = MagicMock(prompt_tokens=11, completion_tokens=4)
    sdk.chat.completions.create.return_value = response
    return sdk


class TestProtocolAndIdentity:
    def test_satisfies_llm_client_protocol(self) -> None:
        client = OpenRouterClient(model=MODEL, sdk_client=_sdk())
        assert isinstance(client, LLMClient)
        assert client.provider_name == "openrouter"
        assert client.model == MODEL


class TestRequestTranslation:
    def test_system_prompt_becomes_leading_system_message(self) -> None:
        sdk = _sdk()
        client = OpenRouterClient(model=MODEL, sdk_client=sdk)
        client.complete(
            LLMRequest(
                system="Be terse.",
                messages=[LLMMessage(role="user", content="hello")],
                temperature=0.3,
                max_output_tokens=1234,
            )
        )
        kwargs = sdk.chat.completions.create.call_args.kwargs
        assert kwargs["messages"][0] == {"role": "system", "content": "Be terse."}
        assert kwargs["messages"][1] == {"role": "user", "content": "hello"}
        assert kwargs["model"] == MODEL
        assert kwargs["temperature"] == 0.3
        assert kwargs["max_tokens"] == 1234

    def test_no_system_message_when_absent(self) -> None:
        sdk = _sdk()
        OpenRouterClient(model=MODEL, sdk_client=sdk).complete(
            LLMRequest(messages=[LLMMessage(role="user", content="hi")])
        )
        roles = [m["role"] for m in sdk.chat.completions.create.call_args.kwargs["messages"]]
        assert roles == ["user"]


class TestResponseTranslation:
    def test_maps_text_model_usage_and_stop_reason(self) -> None:
        client = OpenRouterClient(model=MODEL, sdk_client=_sdk(content="RESULT"))
        response = client.complete(LLMRequest(messages=[LLMMessage(role="user", content="hi")]))
        assert response.text == "RESULT"
        assert response.model == MODEL
        assert response.usage.input_tokens == 11
        assert response.usage.output_tokens == 4
        assert response.stop_reason == "stop"

    def test_null_model_falls_back_to_requested_model(self) -> None:
        # Regression: OpenRouter returned model=None in production, which the
        # SDK types say cannot happen; LLMResponse.model requires a string.
        client = OpenRouterClient(model=MODEL, sdk_client=_sdk(model=None))
        response = client.complete(LLMRequest(messages=[LLMMessage(role="user", content="hi")]))
        assert response.model == MODEL

    def test_null_finish_reason_becomes_empty_string(self) -> None:
        client = OpenRouterClient(model=MODEL, sdk_client=_sdk(finish=None))
        response = client.complete(LLMRequest(messages=[LLMMessage(role="user", content="hi")]))
        assert response.stop_reason == ""

    def test_empty_content_becomes_empty_string(self) -> None:
        client = OpenRouterClient(model=MODEL, sdk_client=_sdk(content=""))
        response = client.complete(LLMRequest(messages=[LLMMessage(role="user", content="hi")]))
        assert response.text == ""


class TestErrorHandling:
    def test_sdk_errors_are_wrapped(self) -> None:
        sdk = MagicMock()
        sdk.chat.completions.create.side_effect = OpenAIError("upstream exploded")
        client = OpenRouterClient(model=MODEL, sdk_client=sdk)
        with pytest.raises(LLMProviderError, match="openrouter"):
            client.complete(LLMRequest(messages=[LLMMessage(role="user", content="hi")]))

    def test_missing_api_key_fails_fast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with pytest.raises(ConfigurationError, match="OPENROUTER_API_KEY"):
            OpenRouterClient(model=MODEL)
