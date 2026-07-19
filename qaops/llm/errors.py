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

    def __init__(self, schema_name: str, attempts: int, raw_responses: list[str]) -> None:
        self.schema_name = schema_name
        self.attempts = attempts
        self.raw_responses = raw_responses
        super().__init__(
            f"Model output failed validation against {schema_name} after {attempts} attempt(s)."
        )
