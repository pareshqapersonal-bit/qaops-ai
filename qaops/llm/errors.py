"""LLM-specific errors.

Both derive from core.errors.LLMError, so callers may catch the broad
type at the pipeline boundary or the specific type when the distinction
matters (provider failure vs. unusable output).
"""

from qaops.core.errors import LLMError


def extract_openai_error_fields(exc: BaseException) -> tuple[int | None, str | None]:
    """Pull sanitized (status_code, error_code) from an OpenAI-SDK exception.

    The OpenAI SDK (used for Groq and OpenRouter) raises APIStatusError
    subclasses carrying ``status_code`` and a structured ``body``/``code``. We
    read only the HTTP status and the machine error code/type - never headers,
    keys, or the full body - so the classifier can distinguish a 429 from a 402
    or 404 even when the message text is opaque (ADR-035). Returns (None, None)
    when the exception is not an OpenAI status error or exposes no such fields.
    """
    status_code: int | None = None
    error_code: str | None = None
    raw_status = getattr(exc, "status_code", None)
    if isinstance(raw_status, int):
        status_code = raw_status
    # `code` is often set directly on the SDK exception; otherwise it lives in
    # body["error"]["code"] or ["type"]. Read defensively and keep it short.
    raw_code = getattr(exc, "code", None)
    if isinstance(raw_code, str) and raw_code:
        error_code = raw_code
    else:
        body = getattr(exc, "body", None)
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                candidate = err.get("code") or err.get("type")
                if isinstance(candidate, str) and candidate:
                    error_code = candidate
    if isinstance(error_code, str) and len(error_code) > 64:
        error_code = error_code[:64]
    return status_code, error_code


class LLMProviderError(LLMError):
    """The provider API failed (auth, rate limit, network, server error).

    Optionally carries sanitized structured fields from the provider SDK
    exception (ADR-035): ``status_code`` (HTTP status) and ``error_code`` (the
    provider's machine error code/type, e.g. ``rate_limit_exceeded``). These let
    the failure classifier decide reliably even when the human message text does
    not contain a recognizable substring - which is what produced the Phase 20
    ``rate_limit -> unknown`` sequence. Only these normalized, non-sensitive
    fields are kept; never headers, keys, or full request/response bodies.
    """

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
    ) -> None:
        self.provider = provider
        self.status_code = status_code
        self.error_code = error_code
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
