"""GroqClient unit tests (ADR-034). No live calls: a stub SDK is injected."""

from types import SimpleNamespace

import pytest

from qaops.core.errors import ConfigurationError
from qaops.llm.errors import LLMProviderError
from qaops.llm.groq_client import GroqClient
from qaops.llm.models import LLMMessage, LLMRequest


class _StubCompletions:
    def __init__(self, response: object | Exception) -> None:
        self._response = response
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


class _StubSDK:
    def __init__(self, response: object | Exception) -> None:
        self.chat = SimpleNamespace(completions=_StubCompletions(response))


def _ok_response() -> object:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content='{"ok": true}'),
                finish_reason="stop",
            )
        ],
        model="llama-3.3-70b-versatile",
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


def _request() -> LLMRequest:
    return LLMRequest(system="sys", messages=[LLMMessage(role="user", content="hi")])


def test_provider_name_and_model() -> None:
    client = GroqClient(model="llama-3.3-70b-versatile", sdk_client=_StubSDK(_ok_response()))
    assert client.provider_name == "groq"
    assert client.model == "llama-3.3-70b-versatile"


def test_complete_returns_text_and_usage() -> None:
    stub = _StubSDK(_ok_response())
    client = GroqClient(model="llama-3.3-70b-versatile", sdk_client=stub)
    response = client.complete(_request())
    assert response.text == '{"ok": true}'
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 5
    assert response.stop_reason == "stop"
    # System prompt is mapped to a leading system message.
    sent = stub.chat.completions.calls[0]
    assert sent["model"] == "llama-3.3-70b-versatile"
    messages = sent["messages"]
    assert messages[0] == {"role": "system", "content": "sys"}


def test_provider_errors_are_wrapped() -> None:
    from openai import OpenAIError

    client = GroqClient(model="llama-3.3-70b-versatile", sdk_client=_StubSDK(OpenAIError("boom")))
    with pytest.raises(LLMProviderError) as exc:
        client.complete(_request())
    assert "groq" in str(exc.value).casefold()


def test_missing_key_raises_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    # No injected client -> constructor resolves the key and must fail fast.
    with pytest.raises(ConfigurationError, match="GROQ_API_KEY"):
        GroqClient(model="llama-3.3-70b-versatile")


def test_factory_builds_groq_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    from qaops.config import QAOpsSettings
    from qaops.llm.factory import create_client

    client = create_client(QAOpsSettings(provider="groq", groq_model="llama-3.1-8b-instant"))
    assert client.provider_name == "groq"
    assert client.model == "llama-3.1-8b-instant"
