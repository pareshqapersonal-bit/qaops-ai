"""GroqClient - an LLMClient backed by Groq via the OpenAI SDK (ADR-034).

Groq exposes an OpenAI-compatible endpoint at https://api.groq.com/openai/v1,
so this reuses the exact pattern of OpenRouterClient: translate an LLMRequest to
an OpenAI chat-completion call, extract text, and wrap every SDK failure in
LLMProviderError. No new SDK dependency is introduced - the openai package
already ships with the openrouter extra.

The API key is resolved from GROQ_API_KEY at construction and fails fast with
ConfigurationError when absent (env only, never config files). A missing key
therefore makes Groq unavailable rather than crashing a run: the registry's
api_key_present() gate keeps Groq out of the provider list when the variable is
unset, and this constructor is only reached once Groq has been selected.

An explicit sdk_client can be injected for testing; no live call is ever made
from unit tests.
"""

import os

from openai import AsyncOpenAI, OpenAI, OpenAIError

from qaops.core.errors import ConfigurationError
from qaops.llm.deadline import HardDeadlineExceeded, run_with_deadline
from qaops.llm.errors import LLMProviderError, extract_openai_error_fields
from qaops.llm.models import LLMRequest, LLMResponse, LLMUsage
from qaops.llm.timeouts import normalize_timeout_message

_KEY_ENV_VAR = "GROQ_API_KEY"
_BASE_URL = "https://api.groq.com/openai/v1"


def _resolve_api_key() -> str:
    value = os.environ.get(_KEY_ENV_VAR, "").strip()
    if value:
        return value
    msg = (
        "Groq API key not found. Set the GROQ_API_KEY environment variable. "
        "Keys are never read from QAOps config files."
    )
    raise ConfigurationError(msg)


class GroqClient:
    """LLMClient implementation backed by Groq's OpenAI-compatible API."""

    def __init__(
        self,
        model: str,
        *,
        timeout_seconds: float = 60.0,
        sdk_client: OpenAI | None = None,
        async_sdk_client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        self._deadline_seconds = timeout_seconds
        # A hard wall-clock deadline is enforced separately via run_with_deadline
        # because httpx has no total-deadline concept (ADR-031). SDK retries are
        # disabled so one QAOps call is one network request (ADR-030).
        self._sync_sdk = sdk_client
        self._async_sdk: AsyncOpenAI | None
        if async_sdk_client is not None:
            self._async_sdk = async_sdk_client
        elif sdk_client is not None:
            self._async_sdk = None
        else:
            self._async_sdk = AsyncOpenAI(
                api_key=_resolve_api_key(),
                base_url=_BASE_URL,
                timeout=timeout_seconds,
                max_retries=0,
            )

    @property
    def provider_name(self) -> str:
        return "groq"

    @property
    def model(self) -> str:
        return self._model

    def complete(self, request: LLMRequest) -> LLMResponse:
        messages: list[dict[str, str]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend({"role": m.role, "content": m.content} for m in request.messages)

        # A sync stub injected by a test: call it directly, no event loop.
        if self._async_sdk is None and self._sync_sdk is not None:
            try:
                response = self._sync_sdk.chat.completions.create(
                    model=self._model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=request.temperature,
                    max_tokens=request.max_output_tokens,
                )
            except OpenAIError as exc:
                status_code, error_code = extract_openai_error_fields(exc)
                raise LLMProviderError(
                    "groq",
                    normalize_timeout_message("groq", exc),
                    status_code=status_code,
                    error_code=error_code,
                ) from exc
            return self._to_response(response)

        async def _call() -> LLMResponse:
            assert self._async_sdk is not None
            response = await self._async_sdk.chat.completions.create(
                model=self._model,
                messages=messages,  # type: ignore[arg-type]
                temperature=request.temperature,
                max_tokens=request.max_output_tokens,
            )
            return self._to_response(response)

        try:
            return run_with_deadline(
                _call, provider="groq", deadline_seconds=self._deadline_seconds
            )
        except HardDeadlineExceeded as exc:
            raise LLMProviderError("groq", str(exc)) from exc
        except OpenAIError as exc:
            status_code, error_code = extract_openai_error_fields(exc)
            raise LLMProviderError(
                "groq",
                normalize_timeout_message("groq", exc),
                status_code=status_code,
                error_code=error_code,
            ) from exc

    def _to_response(self, response: object) -> LLMResponse:
        choice = response.choices[0] if response.choices else None  # type: ignore[attr-defined]
        text = (choice.message.content or "") if choice else ""
        finish_reason = (choice.finish_reason or "") if choice else ""
        model_name = response.model or self._model  # type: ignore[attr-defined]
        usage = response.usage  # type: ignore[attr-defined]
        return LLMResponse(
            text=text,
            model=model_name,
            usage=LLMUsage(
                input_tokens=(usage.prompt_tokens or 0) if usage else 0,
                output_tokens=(usage.completion_tokens or 0) if usage else 0,
            ),
            stop_reason=finish_reason,
        )
