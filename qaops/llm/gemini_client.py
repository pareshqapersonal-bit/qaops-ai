"""GeminiClient - the second real implementation of LLMClient (ADR-013).

Exactly as thin as AnthropicClient: translates LLMRequest to the Gemini
generate_content API, extracts text, and wraps every SDK failure in
LLMProviderError. Schema retries stay in the provider-agnostic
structured-output helper; nothing outside qaops/llm/ imports this
module or the google-genai SDK.

The API key is resolved from GEMINI_API_KEY (or GOOGLE_API_KEY) at
construction and fails fast with ConfigurationError when absent
(ADR-009: env only, never config files). An explicit sdk_client can be
injected for testing.
"""

import base64
import os
from typing import TYPE_CHECKING

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from qaops.core.errors import ConfigurationError
from qaops.llm.errors import LLMProviderError
from qaops.llm.models import LLMRequest, LLMResponse, LLMUsage
from qaops.llm.timeouts import normalize_timeout_message

if TYPE_CHECKING:
    from qaops.llm.models import LLMMessage

_KEY_ENV_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY")


def _message_parts(message: "LLMMessage") -> list[genai_types.Part]:
    """Build the Gemini Part list for one message: text first, then any images.

    Text-only messages produce a single text Part - byte-identical to the prior
    behavior. When the message carries ImageParts (the shared LLMMessage.images
    transport, unchanged), each is appended as a Gemini inline-data Part via the
    SDK's native Part.from_bytes: the base64 ImagePart.data is decoded to raw bytes
    (exact, no re-encode) and media_type is passed through as mime_type. Image list
    order is preserved, matching the NVIDIA client. ImagePart/EvidencePackage/
    LLMMessage are only read here, never modified.
    """
    parts: list[genai_types.Part] = [genai_types.Part(text=message.content)]
    for image in message.images:
        parts.append(
            genai_types.Part.from_bytes(
                data=base64.b64decode(image.data),
                mime_type=image.media_type,
            )
        )
    return parts


def _resolve_api_key() -> str:
    for var in _KEY_ENV_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            return value
    msg = (
        "Gemini API key not found. Set the GEMINI_API_KEY (or GOOGLE_API_KEY) "
        "environment variable. Keys are never read from QAOps config files."
    )
    raise ConfigurationError(msg)


class GeminiClient:
    """LLMClient implementation backed by the Gemini generate_content API."""

    def __init__(
        self,
        model: str,
        *,
        timeout_seconds: float = 60.0,
        sdk_client: genai.Client | None = None,
    ) -> None:
        self._model = model
        if sdk_client is not None:
            self._sdk = sdk_client
        else:
            self._sdk = genai.Client(
                api_key=_resolve_api_key(),
                http_options=genai_types.HttpOptions(timeout=int(timeout_seconds * 1000)),
            )

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def model(self) -> str:
        return self._model

    @property
    def supports_images(self) -> bool:
        """Gemini can consume image inputs via the SDK's inline-data parts.

        The structured-output layer gates image requests on this flag. Gemini's
        Flash models are natively multimodal, so the client can genuinely transport
        image payloads (see _message_parts). Whether a given run actually routes
        images to Gemini is decided upstream by capability-based candidate selection
        (a model's images_supported flag), not here.
        """
        return True

    def complete(self, request: LLMRequest) -> LLMResponse:
        # Gemini's conversation roles are "user" and "model". The variable
        # element type is the SDK ContentUnionDict union because list
        # is invariant and generate_content expects the full union type.
        contents: list[genai_types.ContentUnionDict] = [
            genai_types.Content(
                role="user" if message.role == "user" else "model",
                parts=_message_parts(message),
            )
            for message in request.messages
        ]
        config = genai_types.GenerateContentConfig(
            system_instruction=request.system or None,
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
        )
        try:
            response = self._sdk.models.generate_content(
                model=self._model, contents=contents, config=config
            )
        except genai_errors.APIError as exc:
            raise LLMProviderError("gemini", normalize_timeout_message("gemini", exc)) from exc

        usage = response.usage_metadata
        candidates = response.candidates or []
        finish_reason = ""
        if candidates and candidates[0].finish_reason is not None:
            finish_reason = candidates[0].finish_reason.name
        return LLMResponse(
            text=response.text or "",
            model=self._model,
            usage=LLMUsage(
                input_tokens=(usage.prompt_token_count or 0) if usage else 0,
                output_tokens=(usage.candidates_token_count or 0) if usage else 0,
            ),
            stop_reason=finish_reason,
        )
