"""GeminiClient and provider-factory tests. All offline (ADR-008):
the SDK client is stubbed; no network calls, no real keys."""

from typing import Any

import pytest
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from qaops.config import QAOpsSettings
from qaops.core.errors import ConfigurationError
from qaops.llm import (
    AnthropicClient,
    LLMClient,
    LLMMessage,
    LLMProviderError,
    LLMRequest,
    create_client,
)
from qaops.llm.gemini_client import GeminiClient


def make_sdk_response(
    text: str = "hello", finish: genai_types.FinishReason = genai_types.FinishReason.STOP
) -> genai_types.GenerateContentResponse:
    return genai_types.GenerateContentResponse(
        candidates=[
            genai_types.Candidate(
                content=genai_types.Content(role="model", parts=[genai_types.Part(text=text)]),
                finish_reason=finish,
            )
        ],
        usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
            prompt_token_count=12, candidates_token_count=7
        ),
        model_version="gemini-2.5-flash",
    )


class StubModels:
    def __init__(self, result: genai_types.GenerateContentResponse | Exception) -> None:
        self._result = result
        self.kwargs: dict[str, Any] = {}

    def generate_content(self, **kwargs: Any) -> genai_types.GenerateContentResponse:
        self.kwargs = kwargs
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class StubSdk:
    def __init__(self, result: genai_types.GenerateContentResponse | Exception) -> None:
        self.models = StubModels(result)


def make_client(
    result: genai_types.GenerateContentResponse | Exception,
) -> tuple[GeminiClient, StubModels]:
    sdk = StubSdk(result)
    client = GeminiClient(model="gemini-2.5-flash", sdk_client=sdk)  # type: ignore[arg-type]
    return client, sdk.models


def make_request(system: str = "") -> LLMRequest:
    return LLMRequest(
        system=system,
        messages=[
            LLMMessage(role="user", content="hi"),
            LLMMessage(role="assistant", content="hello"),
            LLMMessage(role="user", content="continue"),
        ],
        temperature=0.3,
        max_output_tokens=512,
    )


class TestGeminiClient:
    def test_satisfies_protocol(self) -> None:
        client, _ = make_client(make_sdk_response())
        assert isinstance(client, LLMClient)
        assert client.provider_name == "gemini"

    def test_translates_request(self) -> None:
        client, stub = make_client(make_sdk_response())
        client.complete(make_request(system="You are terse."))

        assert stub.kwargs["model"] == "gemini-2.5-flash"
        contents = stub.kwargs["contents"]
        assert [c.role for c in contents] == ["user", "model", "user"]  # assistant -> model
        assert contents[0].parts[0].text == "hi"
        config = stub.kwargs["config"]
        assert config.system_instruction == "You are terse."
        assert config.temperature == 0.3
        assert config.max_output_tokens == 512

    def test_empty_system_becomes_none(self) -> None:
        client, stub = make_client(make_sdk_response())
        client.complete(make_request(system=""))
        assert stub.kwargs["config"].system_instruction is None

    def test_translates_response(self) -> None:
        client, _ = make_client(make_sdk_response(text="pong"))
        response = client.complete(make_request())
        assert response.text == "pong"
        assert response.model == "gemini-2.5-flash"
        assert response.usage.input_tokens == 12
        assert response.usage.output_tokens == 7
        assert response.stop_reason == "STOP"

    def test_wraps_sdk_errors_in_provider_error(self) -> None:
        sdk_error = genai_errors.APIError(429, {"error": {"message": "quota exceeded"}})
        client, _ = make_client(sdk_error)
        with pytest.raises(LLMProviderError, match=r"\[gemini\]"):
            client.complete(make_request())

    def test_missing_api_key_fails_fast_at_construction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
            GeminiClient(model="gemini-2.5-flash")

    def test_key_from_env_constructs_real_sdk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-never-used")
        client = GeminiClient(model="gemini-2.5-flash")  # no network at construction
        assert isinstance(client._sdk, genai.Client)  # noqa: SLF001 - construction check


class TestCreateClient:
    def test_default_settings_build_anthropic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("QAOPS_PROVIDER", raising=False)
        client = create_client(QAOpsSettings())
        assert isinstance(client, AnthropicClient)
        assert client.model == QAOpsSettings().model

    def test_env_switch_builds_gemini_without_code_changes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("QAOPS_PROVIDER", "gemini")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-never-used")
        client = create_client(QAOpsSettings())
        assert isinstance(client, GeminiClient)
        assert client.model == "gemini-2.5-flash"

    def test_gemini_model_setting_is_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QAOPS_PROVIDER", "gemini")
        monkeypatch.setenv("QAOPS_GEMINI_MODEL", "gemini-2.5-pro")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-never-used")
        client = create_client(QAOpsSettings())
        assert isinstance(client, GeminiClient)
        assert client.model == "gemini-2.5-pro"

    def test_mock_provider_is_rejected_by_factory(self) -> None:
        with pytest.raises(ConfigurationError, match="construct\\s+MockLLMClient"):
            create_client(QAOpsSettings(provider="mock"))

    def test_unknown_provider_rejected_by_settings(self) -> None:
        with pytest.raises(ValueError, match="Unknown provider"):
            QAOpsSettings(provider="openai")
