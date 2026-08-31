"""NvidiaClient - an LLMClient backed by NVIDIA's OpenAI-compatible API.

Cloned from OpenRouterClient (both use the OpenAI SDK against a configurable base
URL); the only substantive addition is multimodal message conversion. When a
message carries ImageParts (the Phase 36A/36B transport seam), they are appended as
OpenAI-style vision content parts alongside the text; text-only messages keep the
plain-string content shape, byte-identical to the other OpenAI-compatible providers.

The provider declares supports_images = True, so the structured-output layer's
hard-fail (which blocks image requests to text-only providers) lets NVIDIA image
requests through. Schema retries stay in the provider-agnostic structured-output
helper; nothing here enforces JSON natively.

The API key is resolved from NVIDIA_API_KEY at construction and fails fast with
ConfigurationError when absent (ADR-009: env only, never config files). An explicit
sdk_client can be injected for testing. Base URL and model are configurable.
"""

import os
from typing import TYPE_CHECKING

from openai import AsyncOpenAI, OpenAI, OpenAIError

from qaops.core.errors import ConfigurationError
from qaops.llm.deadline import HardDeadlineExceeded, run_with_deadline
from qaops.llm.errors import LLMProviderError, extract_openai_error_fields
from qaops.llm.models import LLMRequest, LLMResponse, LLMUsage
from qaops.llm.timeouts import normalize_timeout_message

if TYPE_CHECKING:
    from qaops.llm.models import ImagePart, LLMMessage

_KEY_ENV_VAR = "NVIDIA_API_KEY"
_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
_DEFAULT_MODEL = "nvidia/nemotron-nano-12b-v2-vl"


def _resolve_api_key() -> str:
    value = os.environ.get(_KEY_ENV_VAR, "").strip()
    if value:
        return value
    msg = (
        "NVIDIA API key not found. Set the NVIDIA_API_KEY environment variable. "
        "Keys are never read from QAOps config files."
    )
    raise ConfigurationError(msg)


def _image_data_uri(image: "ImagePart") -> str:
    """Build an OpenAI-style data URI from an ImagePart, preserving base64 exactly.

    The base64 payload is used verbatim (no decode/re-encode); the media_type comes
    straight from the ImagePart. source_filename is deliberately NOT placed in the
    URI.
    """
    return f"data:{image.media_type};base64,{image.data}"


def _message_to_openai(message: "LLMMessage") -> dict[str, object]:
    """Convert one LLMMessage to an OpenAI chat message.

    Text-only messages keep the plain-string content shape (identical to the other
    OpenAI-compatible providers). When images are present, content becomes a parts
    array: the text part first, then one image_url part per image in order, so all
    images are preserved and ordering is deterministic.
    """
    if not message.images:
        return {"role": message.role, "content": message.content}
    parts: list[dict[str, object]] = [{"type": "text", "text": message.content}]
    parts.extend(
        {"type": "image_url", "image_url": {"url": _image_data_uri(image)}}
        for image in message.images
    )
    return {"role": message.role, "content": parts}


class NvidiaClient:
    """LLMClient implementation backed by NVIDIA's OpenAI-compatible API (Nemotron)."""

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        timeout_seconds: float = 60.0,
        sdk_client: OpenAI | None = None,
        async_sdk_client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        self._base_url = base_url
        self._deadline_seconds = timeout_seconds
        self._sync_sdk = sdk_client  # retained for injection/back-compat in tests
        self._async_sdk: AsyncOpenAI | None
        if async_sdk_client is not None:
            self._async_sdk = async_sdk_client
        elif sdk_client is not None:
            self._async_sdk = None
        else:
            self._async_sdk = AsyncOpenAI(
                api_key=_resolve_api_key(),
                base_url=base_url,
                timeout=timeout_seconds,
                # QAOps owns retries (ADR-030); one QAOps call is one request.
                max_retries=0,
            )

    @property
    def provider_name(self) -> str:
        return "nvidia"

    @property
    def model(self) -> str:
        return self._model

    @property
    def supports_images(self) -> bool:
        return True

    def complete(self, request: LLMRequest) -> LLMResponse:
        messages: list[dict[str, object]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        messages.extend(_message_to_openai(m) for m in request.messages)

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
                    "nvidia",
                    normalize_timeout_message("nvidia", exc),
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
                _call, provider="nvidia", deadline_seconds=self._deadline_seconds
            )
        except HardDeadlineExceeded as exc:
            raise LLMProviderError("nvidia", str(exc)) from exc
        except OpenAIError as exc:
            status_code, error_code = extract_openai_error_fields(exc)
            raise LLMProviderError(
                "nvidia",
                normalize_timeout_message("nvidia", exc),
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
