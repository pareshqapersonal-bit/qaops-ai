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
        self.truncated = truncated
        empty_count = sum(1 for raw in raw_responses if not raw.strip())
        message = (
            f"Model output failed validation against {schema_name} after {attempts} attempt(s)."
        )
        if truncated:
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
