"""LLM-specific errors.

Both derive from core.errors.LLMError, so callers may catch the broad
type at the pipeline boundary or the specific type when the distinction
matters (provider failure vs. unusable output).
"""

from qaops.core.errors import LLMError


class LLMProviderError(LLMError):
    """The provider API failed (auth, rate limit, network, server error)."""

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        super().__init__(f"[{provider}] {message}")


class LLMEmptyResponseError(LLMError):
    """The provider returned no content at all.

    Distinct from LLMResponseFormatError: there is nothing to parse or repair.
    An empty response is never diagnosed as token truncation, even when
    stop_reason is "length" - zero characters is not evidence that a useful
    output was cut off by the configured cap (ADR-030). The executor classifies
    this as EMPTY_OUTPUT and moves to the next model rather than re-rolling.
    """

    def __init__(self, schema_name: str, attempt: int, provider: str, model: str) -> None:
        self.schema_name = schema_name
        self.attempt = attempt
        self.provider = provider
        self.model = model
        super().__init__(
            f"Model returned no content for {schema_name} "
            f"(provider={provider}, model={model}). The provider returned an "
            "empty response - this usually means model capacity limits, rate "
            "limiting, or a free-tier model declining to answer. Trying another "
            "model is more useful than retrying this one."
        )


class LLMResponseFormatError(LLMError):
    """The model's output failed schema validation after all retries.

    Carries every raw response attempted so the failure is debuggable
    (ADR-002: fail loudly, never fall back silently).
    """

    def __init__(
        self,
        schema_name: str,
        attempts: int,
        raw_responses: list[str],
        *,
        truncated: bool = False,
    ) -> None:
        self.schema_name = schema_name
        self.attempts = attempts
        self.raw_responses = raw_responses
        empty_count = sum(1 for raw in raw_responses if not raw.strip())
        # Truncation advice is only accurate when the last response actually had
        # content that was cut off. A caller may set truncated=True from a bare
        # stop_reason; if every response was empty, that is an empty-output
        # failure, not truncation, so never recommend raising the token cap
        # (ADR-030).
        self.truncated = truncated and empty_count < len(raw_responses)
        message = (
            f"Model output failed validation against {schema_name} after {attempts} attempt(s)."
        )
        if self.truncated:
            message += (
                " The response was cut off by the output token limit - the model produced "
                "valid output that did not fit. Raise max_output_tokens in qaops.yaml "
                "(e.g. 32000) and retry."
            )
        elif empty_count:
            message += (
                f" {empty_count} of {len(raw_responses)} response(s) were empty - the provider "
                "returned no content. This usually means model capacity limits, rate limiting, "
                "or a free-tier model declining to answer; try a different or more capable model."
            )
        super().__init__(message)
