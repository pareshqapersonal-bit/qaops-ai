"""AnthropicClient - the V1 real implementation of LLMClient (ADR-002).

A thin wrapper: translates LLMRequest to the Anthropic Messages API,
extracts text blocks, and wraps every SDK failure in LLMProviderError.
No prompt logic, no parsing, no retry-on-invalid-schema here - schema
retries belong to the structured-output helper, which is provider-
agnostic. QAOps owns retries (ADR-030), so the SDK's own transport retries
are disabled by default (max_transport_retries=0).

The API key is resolved by the SDK from ANTHROPIC_API_KEY (ADR-009);
it is never accepted through QAOps config files. An explicit sdk_client
can be injected for testing.
"""

import anthropic

from qaops.llm.errors import LLMProviderError
from qaops.llm.models import LLMRequest, LLMResponse, LLMUsage
from qaops.llm.timeouts import normalize_timeout_message


class AnthropicClient:
    """LLMClient implementation backed by the Anthropic Messages API."""

    def __init__(
        self,
        model: str,
        *,
        timeout_seconds: float = 60.0,
        max_transport_retries: int = 0,
        sdk_client: anthropic.Anthropic | None = None,
    ) -> None:
        self._model = model
        self._sdk = sdk_client or anthropic.Anthropic(
            timeout=timeout_seconds,
            max_retries=max_transport_retries,
        )

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def model(self) -> str:
        return self._model

    def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            message = self._sdk.messages.create(
                model=self._model,
                system=request.system or anthropic.omit,
                messages=[{"role": m.role, "content": m.content} for m in request.messages],
                temperature=request.temperature,
                max_tokens=request.max_output_tokens,
            )
        except anthropic.APIError as exc:
            raise LLMProviderError(
                "anthropic", normalize_timeout_message("anthropic", exc)
            ) from exc

        text = "".join(
            block.text for block in message.content if isinstance(block, anthropic.types.TextBlock)
        )
        return LLMResponse(
            text=text,
            model=message.model,
            usage=LLMUsage(
                input_tokens=message.usage.input_tokens,
                output_tokens=message.usage.output_tokens,
            ),
            stop_reason=message.stop_reason or "",
        )
