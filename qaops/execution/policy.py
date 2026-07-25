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
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CONTEXT_LIMIT = "context_limit"
    MODEL_UNAVAILABLE = "model_unavailable"
    INVALID_OUTPUT = "invalid_output"
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
        action=Action.RETRY_SAME,
        kind=FailureKind.INVALID_OUTPUT,
        explanation="model output failed schema validation; retrying the same model",
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


def recovery_for(message: str) -> Recovery:
    """Classify a failure and return the policy response."""
    return _POLICY[classify_failure(message)]
