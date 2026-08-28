"""Failure classification and recovery policy (ADR-026).

Different provider failures need different responses. A timeout is worth
retrying on the same provider; exhausted credit never is. Treating every error
identically either wastes time retrying the hopeless or gives up on the
recoverable.

Classification reuses the CLI's existing text-based diagnosis patterns
(ADR-023) rather than a second set: one place decides what a provider error
means. This module adds the *policy* - what to do about it.
"""

from dataclasses import dataclass
from enum import StrEnum


class FailureKind(StrEnum):
    """What went wrong, in terms that determine recovery."""

    AUTHENTICATION = "authentication"
    INSUFFICIENT_CREDIT = "insufficient_credit"
    PROVIDER_RATE_LIMIT = "provider_rate_limit"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CONTEXT_LIMIT = "context_limit"
    MODEL_UNAVAILABLE = "model_unavailable"
    INVALID_OUTPUT = "invalid_output"
    EMPTY_OUTPUT = "empty_output"
    UNKNOWN = "unknown"


class Action(StrEnum):
    """How the executor should respond to a failure."""

    RETRY_SAME = "retry_same"
    RETRY_SAME_WITH_BACKOFF = "retry_same_with_backoff"
    NEXT_MODEL = "next_model"
    DROP_MODEL_AND_CONTINUE = "drop_model_and_continue"
    LARGER_CONTEXT_MODEL = "larger_context_model"
    SWITCH_PROVIDER = "switch_provider"
    DISABLE_AND_SWITCH = "disable_and_switch"
    ABORT = "abort"


@dataclass(frozen=True)
class Recovery:
    """The policy response to one failure."""

    action: Action
    kind: FailureKind
    explanation: str
    backoff_seconds: float = 0.0

    @property
    def disables_provider(self) -> bool:
        """True when the provider should be skipped for the rest of the run."""
        return self.action is Action.DISABLE_AND_SWITCH

    @property
    def disables_model(self) -> bool:
        """True when this model should be dropped from the candidate list."""
        return self.action in {
            Action.NEXT_MODEL,
            Action.DROP_MODEL_AND_CONTINUE,
            Action.LARGER_CONTEXT_MODEL,
        }

    @property
    def tries_another_model(self) -> bool:
        """True when recovery stays within the provider, on a different model."""
        return self.action in {
            Action.NEXT_MODEL,
            Action.DROP_MODEL_AND_CONTINUE,
            Action.LARGER_CONTEXT_MODEL,
        }


# Substrings that identify each failure kind, matched case-insensitively
# against the provider's own error text. Ordered most specific first.
_PATTERNS: tuple[tuple[FailureKind, tuple[str, ...]], ...] = (
    (
        FailureKind.INSUFFICIENT_CREDIT,
        ("more credits", "insufficient credit", "insufficient_quota", "can only afford", "402"),
    ),
    (
        FailureKind.AUTHENTICATION,
        ("invalid x-api-key", "authentication", "unauthorized", "invalid api key", "401", "403"),
    ),
    (
        # Account-wide (provider-level) exhaustion, distinct from a per-model or
        # transient rate limit. OpenRouter's free daily cap is shared across ALL
        # :free models, so its "free-models-per-day" 429 means no further free
        # call on this provider will succeed this run - retrying other models
        # only wastes calls (ADR-034). Gemini's free-tier daily cap is the same
        # shape: a 429 whose quota id is "GenerateRequestsPerDayPerProject-FreeTier"
        # (metric generate_content_free_tier_requests) is a per-project/day limit
        # shared across every Gemini model, so the provider is done for the run.
        # The needles below are the daily-specific wording only ("requestsperday",
        # "perday"); they deliberately do NOT include "resource_exhausted" (which
        # also appears on transient per-MINUTE limits like RequestsPerMinutePer
        # Project) so a short-window 429 stays transient. Matched BEFORE the generic
        # RATE_LIMIT patterns so it wins. Kept deliberately specific to daily/account
        # exhaustion wording, never plain "429" or "rate limited".
        FailureKind.PROVIDER_RATE_LIMIT,
        (
            "free-models-per-day",
            "free model requests per day",
            "requests per day",
            "requestsperday",
            "perday",
            "daily limit",
            "quota exceeded for",
        ),
    ),
    (
        FailureKind.RATE_LIMIT,
        ("rate-limited", "rate limited", "rate_limit", "429", "too many requests"),
    ),
    (FailureKind.TIMEOUT, ("timeout", "timed out", "deadline exceeded", "connection reset")),
    (
        FailureKind.CONTEXT_LIMIT,
        ("context length", "maximum context", "context_length_exceeded", "too many tokens"),
    ),
    (
        FailureKind.MODEL_UNAVAILABLE,
        ("model is unavailable", "unknown model", "no endpoints found", "404", "model not found"),
    ),
    (
        FailureKind.EMPTY_OUTPUT,
        ("returned no content", "empty response", "empty output", "zero-content"),
    ),
    (
        FailureKind.INVALID_OUTPUT,
        ("failed validation against", "jsondecodeerror", "validationerror", "invalid json"),
    ),
)

# What to do about each kind, and why. Recovery now prefers staying within the
# provider: a model-specific failure (credit on one paid model, an unavailable
# model, a context overflow) should try a sibling model before abandoning a
# provider whose credentials are perfectly good (ADR-027).
_POLICY: dict[FailureKind, Recovery] = {
    FailureKind.INSUFFICIENT_CREDIT: Recovery(
        action=Action.NEXT_MODEL,
        kind=FailureKind.INSUFFICIENT_CREDIT,
        explanation="insufficient credit for this model; trying another model on this provider",
    ),
    FailureKind.AUTHENTICATION: Recovery(
        action=Action.DISABLE_AND_SWITCH,
        kind=FailureKind.AUTHENTICATION,
        explanation="credentials rejected; every model on this provider will fail identically",
    ),
    FailureKind.MODEL_UNAVAILABLE: Recovery(
        action=Action.DROP_MODEL_AND_CONTINUE,
        kind=FailureKind.MODEL_UNAVAILABLE,
        explanation="model unavailable; removing it and trying the next",
    ),
    FailureKind.PROVIDER_RATE_LIMIT: Recovery(
        action=Action.DISABLE_AND_SWITCH,
        kind=FailureKind.PROVIDER_RATE_LIMIT,
        explanation=(
            "provider-wide/account daily quota exhausted; every model on this "
            "provider shares it, so the provider is disabled for the rest of the run"
        ),
    ),
    FailureKind.RATE_LIMIT: Recovery(
        action=Action.RETRY_SAME_WITH_BACKOFF,
        kind=FailureKind.RATE_LIMIT,
        explanation="rate limited; backing off before retrying",
        backoff_seconds=2.0,
    ),
    FailureKind.TIMEOUT: Recovery(
        action=Action.RETRY_SAME,
        kind=FailureKind.TIMEOUT,
        explanation="request timed out; retrying the same model",
    ),
    FailureKind.INVALID_OUTPUT: Recovery(
        # A model reaching the executor with invalid_output has ALREADY
        # exhausted its bounded in-request repair attempts (ADR-030). Handing it
        # another full repair cycle wastes provider calls, so move to the next
        # model rather than retrying the same one.
        action=Action.NEXT_MODEL,
        kind=FailureKind.INVALID_OUTPUT,
        explanation="model could not produce schema-valid output; trying another model",
    ),
    FailureKind.EMPTY_OUTPUT: Recovery(
        # A model returning zero content will not be fixed by a repair prompt -
        # there is nothing to repair. Abandon it for this stage immediately.
        action=Action.NEXT_MODEL,
        kind=FailureKind.EMPTY_OUTPUT,
        explanation="model returned no content; trying another model",
    ),
    FailureKind.CONTEXT_LIMIT: Recovery(
        action=Action.LARGER_CONTEXT_MODEL,
        kind=FailureKind.CONTEXT_LIMIT,
        explanation="request exceeded this model's limit; trying one with more context",
    ),
    FailureKind.UNKNOWN: Recovery(
        action=Action.NEXT_MODEL,
        kind=FailureKind.UNKNOWN,
        explanation="unrecognised failure; trying the next model",
    ),
}


def classify_failure(message: str) -> FailureKind:
    """Identify what kind of failure a provider error message describes."""
    lowered = message.casefold()
    for kind, needles in _PATTERNS:
        if any(needle in lowered for needle in needles):
            return kind
    return FailureKind.UNKNOWN


# HTTP status -> failure kind, used only when message-text matching is
# inconclusive. This is why the Phase 20 "rate_limit -> unknown" sequence could
# happen: a 429 whose body text lacked a known substring fell through to
# UNKNOWN. With the SDK's numeric status preserved (ADR-035), a 429 is reliably
# a rate limit, a 402 insufficient credit, a 404 an unavailable model, a
# 401/403 authentication. 5xx is a transient server error (retry as timeout-like
# backoff). We deliberately do NOT map 429 to provider-wide exhaustion - scope
# is decided separately below.
_STATUS_TO_KIND: dict[int, FailureKind] = {
    401: FailureKind.AUTHENTICATION,
    403: FailureKind.AUTHENTICATION,
    402: FailureKind.INSUFFICIENT_CREDIT,
    404: FailureKind.MODEL_UNAVAILABLE,
    408: FailureKind.TIMEOUT,
    409: FailureKind.RATE_LIMIT,
    429: FailureKind.RATE_LIMIT,
    500: FailureKind.TIMEOUT,
    502: FailureKind.TIMEOUT,
    503: FailureKind.TIMEOUT,
    504: FailureKind.TIMEOUT,
}

# Provider error codes/types that indicate ACCOUNT/PROJECT-wide exhaustion
# rather than a transient or per-model limit. Matched case-insensitively against
# the SDK's structured error code. Kept specific so an ordinary rate_limit code
# is not mistaken for provider-wide exhaustion.
_PROVIDER_WIDE_ERROR_CODES = (
    "insufficient_quota",
    "billing_hard_limit_reached",
    "account_deactivated",
)


def classify_failure_fields(
    message: str,
    *,
    status_code: int | None = None,
    error_code: str | None = None,
) -> FailureKind:
    """Classify using sanitized structured fields first, then message text.

    Order (most reliable first):
    1. A provider-wide error code (insufficient_quota, billing hard limit) ->
       PROVIDER_RATE_LIMIT so the provider is disabled for the run.
    2. Message-text patterns - these carry the existing, well-tested distinctions
       (e.g. OpenRouter's account-wide "free-models-per-day" wording, empty/
       invalid output) that a bare status code cannot express.
    3. HTTP status code - the reliable fallback when the text is opaque, which is
       the Phase 20 fix.
    Falls back to UNKNOWN only when none of the above resolves (ADR-035).
    """
    if error_code:
        lowered_code = error_code.casefold()
        if any(code in lowered_code for code in _PROVIDER_WIDE_ERROR_CODES):
            return FailureKind.PROVIDER_RATE_LIMIT
    text_kind = classify_failure(message)
    if text_kind is not FailureKind.UNKNOWN:
        return text_kind
    if status_code is not None and status_code in _STATUS_TO_KIND:
        return _STATUS_TO_KIND[status_code]
    return FailureKind.UNKNOWN


def recovery_for(message: str) -> Recovery:
    """Classify a failure and return the policy response."""
    return _POLICY[classify_failure(message)]


def recovery_for_exception(exc: BaseException) -> Recovery:
    """Classify an exception (using structured fields when present) and return
    the policy response (ADR-035).

    Reads sanitized ``status_code`` / ``error_code`` off LLMProviderError when
    available; otherwise behaves exactly like ``recovery_for(str(exc))``.
    """
    status_code = getattr(exc, "status_code", None)
    error_code = getattr(exc, "error_code", None)
    kind = classify_failure_fields(
        str(exc),
        status_code=status_code if isinstance(status_code, int) else None,
        error_code=error_code if isinstance(error_code, str) else None,
    )
    return _POLICY[kind]
