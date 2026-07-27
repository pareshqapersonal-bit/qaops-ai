"""Timeout detection at the provider boundary (ADR-030).

Each provider SDK raises its own timeout exception type. The adaptive executor
classifies failures by message text (a "timeout" substring), so a timeout must
reach it as a message it recognises - not a provider-specific type and not an
opaque string that happens to omit the word.

`normalize_timeout_message` inspects an SDK exception and, when it reliably
represents a request deadline, returns a normalized message beginning with
"request timed out". Only exceptions that genuinely mean "deadline exceeded" are
treated as timeouts; arbitrary network errors are left alone so they classify by
their own text (section 6).
"""

# SDK timeout exception type names, matched on the class hierarchy by name so we
# do not hard-depend on optional SDKs being importable. Anthropic raises
# APITimeoutError; the OpenAI SDK (used for OpenRouter) raises APITimeoutError;
# google-genai surfaces deadline errors whose text contains "deadline".
_TIMEOUT_TYPE_NAMES = frozenset(
    {
        "APITimeoutError",  # anthropic and openai SDKs
        "Timeout",
        "TimeoutError",
        "ReadTimeout",
        "ConnectTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "DeadlineExceeded",
    }
)

_TIMEOUT_TEXT_MARKERS = ("timed out", "timeout", "deadline exceeded")


def is_timeout_exception(exc: BaseException) -> bool:
    """Whether an SDK exception reliably represents a request deadline.

    Checks the exception's class hierarchy for a known timeout type name, then
    falls back to unambiguous text markers. Deliberately conservative: a plain
    connection error is not a timeout.
    """
    for klass in type(exc).__mro__:
        if klass.__name__ in _TIMEOUT_TYPE_NAMES:
            return True
    text = str(exc).casefold()
    return any(marker in text for marker in _TIMEOUT_TEXT_MARKERS)


def normalize_timeout_message(provider: str, exc: BaseException) -> str:
    """A message for `exc` that the executor will classify correctly.

    For a genuine timeout, returns text beginning with "request timed out" so
    the policy maps it to FailureKind.TIMEOUT. Otherwise returns the raw string,
    unchanged, to classify by its own content.
    """
    if is_timeout_exception(exc):
        return f"request timed out after the configured deadline ({provider}): {exc}"
    return str(exc)
