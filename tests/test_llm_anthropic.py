"""AnthropicClient tests.

Unit tests inject a stubbed SDK client (no network, no API key). The
single live test is marked @pytest.mark.llm and excluded from CI
(ADR-008); run it locally with `pytest -m llm` and a real key.
"""

import os
from typing import Any

import anthropic
import pytest
from anthropic.types import Message, TextBlock, Usage

from qaops.llm import AnthropicClient, LLMClient, LLMMessage, LLMProviderError, LLMRequest


def make_sdk_message(text: str = "hello", stop_reason: str = "end_turn") -> Message:
    return Message(
        id="msg_test",
        type="message",
        role="assistant",
        model="claude-sonnet-4-6",
        content=[TextBlock(type="text", text=text)],
        stop_reason=stop_reason,  # type: ignore[arg-type]  # str narrows to the SDK Literal
        usage=Usage(input_tokens=12, output_tokens=7),
    )


class StubMessages:
    def __init__(self, result: Message | Exception) -> None:
        self._result = result
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Message:
        self.kwargs = kwargs
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def make_client(result: Message | Exception) -> tuple[AnthropicClient, StubMessages]:
    sdk = anthropic.Anthropic(api_key="test-key-never-used")
    stub = StubMessages(result)
    sdk.messages = stub  # type: ignore[misc, assignment]  # test double replaces the resource
    return AnthropicClient(model="claude-sonnet-4-6", sdk_client=sdk), stub


def make_request(system: str = "") -> LLMRequest:
    return LLMRequest(
        system=system,
        messages=[LLMMessage(role="user", content="hi")],
        temperature=0.3,
        max_output_tokens=512,
    )


class TestAnthropicClient:
    def test_satisfies_protocol(self) -> None:
        client, _ = make_client(make_sdk_message())
        assert isinstance(client, LLMClient)
        assert client.provider_name == "anthropic"

    def test_translates_request_and_response(self) -> None:
        client, stub = make_client(make_sdk_message(text="pong"))
        response = client.complete(make_request(system="You are terse."))

        assert stub.kwargs["model"] == "claude-sonnet-4-6"
        assert stub.kwargs["system"] == "You are terse."
        assert stub.kwargs["messages"] == [{"role": "user", "content": "hi"}]
        assert stub.kwargs["temperature"] == 0.3
        assert stub.kwargs["max_tokens"] == 512

        assert response.text == "pong"
        assert response.usage.input_tokens == 12
        assert response.usage.output_tokens == 7
        assert response.stop_reason == "end_turn"

    def test_empty_system_is_omitted(self) -> None:
        client, stub = make_client(make_sdk_message())
        client.complete(make_request(system=""))
        assert isinstance(stub.kwargs["system"], anthropic.Omit)

    def test_concatenates_multiple_text_blocks(self) -> None:
        msg = make_sdk_message()
        msg.content = [
            TextBlock(type="text", text="part1 "),
            TextBlock(type="text", text="part2"),
        ]
        client, _ = make_client(msg)
        assert client.complete(make_request()).text == "part1 part2"

    def test_wraps_sdk_errors_in_provider_error(self) -> None:
        sdk_error = anthropic.APIConnectionError(request=None)  # type: ignore[arg-type]
        client, _ = make_client(sdk_error)
        with pytest.raises(LLMProviderError, match=r"\[anthropic\]"):
            client.complete(make_request())


@pytest.mark.llm
@pytest.mark.skipif("ANTHROPIC_API_KEY" not in os.environ, reason="requires real API key")
def test_live_completion_round_trip() -> None:
    """Live eval: one tiny real call. Run locally: pytest -m llm"""
    client = AnthropicClient(model="claude-sonnet-4-6")
    response = client.complete(
        LLMRequest(
            messages=[LLMMessage(role="user", content="Reply with exactly the word: pong")],
            max_output_tokens=256,
        )
    )
    assert "pong" in response.text.lower()
    assert response.usage.total_tokens > 0
