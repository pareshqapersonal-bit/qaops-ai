"""OpenRouterClient - an LLMClient backed by OpenRouter via the OpenAI SDK.

Exactly as thin as GeminiClient: translates LLMRequest to an OpenAI
chat-completion call against the OpenRouter base URL, extracts text, and
wraps every SDK failure in LLMProviderError. Schema retries stay in the
provider-agnostic structured-output helper; nothing outside qaops/llm/
imports this module or the openai SDK.

The API key is resolved from OPENROUTER_API_KEY at construction and
fails fast with ConfigurationError when absent (ADR-009: env only, never
config files). An explicit sdk_client can be injected for testing.
"""

import os

from openai import OpenAI, OpenAIError

from qaops.core.errors import ConfigurationError
from qaops.llm.errors import LLMProviderError
from qaops.llm.models import LLMRequest, LLMResponse, LLMUsage

_KEY_ENV_VAR = "OPENROUTER_API_KEY"
_BASE_URL = "https://openrouter.ai/api/v1"


def _resolve_api_key() -> str:
    value = os.environ.get(_KEY_ENV_VAR, "").strip()
    if value:
        return value
    msg = (
        "OpenRouter API key not found. Set the OPENROUTER_API_KEY environment "
        "variable. Keys are never read from QAOps config files."
    )
    raise ConfigurationError(msg)


class OpenRouterClient:
    """LLMClient implementation backed by OpenRouter's OpenAI-compatible API."""

    def __init__(
        self,
        model: str,
        *,
        timeout_seconds: float = 120.0,
        sdk_client: OpenAI | None = None,
    ) -> None:
        self._model = model
        if sdk_client is not None:
            self._sdk = sdk_client
        else:
            self._sdk = OpenAI(
                api_key=_resolve_api_key(),
                base_url=_BASE_URL,
                timeout=timeout_seconds,
            )

    @property
    def provider_name(self) -> str:
        return "openrouter"

    @property
    def model(self) -> str:
        return self._model

    def complete(self, request: LLMRequest) -> LLMResponse:
        # OpenAI chat roles are "system", "user", "assistant". Map the request's
        # system prompt to a leading system message, then the conversation turns.
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend({"role": m.role, "content": m.content} for m in request.messages)

        try:
            response = self._sdk.chat.completions.create(
                model=self._model,
                messages=messages,  # type: ignore[arg-type]  # plain dicts accepted by the SDK
                temperature=request.temperature,
                max_tokens=request.max_output_tokens,
            )
        except OpenAIError as exc:
            raise LLMProviderError("openrouter", str(exc)) from exc

        choice = response.choices[0] if response.choices else None
        text = (choice.message.content or "") if choice else ""
        finish_reason = choice.finish_reason if choice else ""
        usage = response.usage
        return LLMResponse(
            text=text,
            model=response.model,
            usage=LLMUsage(
                input_tokens=(usage.prompt_tokens or 0) if usage else 0,
                output_tokens=(usage.completion_tokens or 0) if usage else 0,
            ),
            stop_reason=finish_reason,
        )
