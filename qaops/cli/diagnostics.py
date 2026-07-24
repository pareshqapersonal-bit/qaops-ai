"""Friendly classification of provider runtime failures (ADR-023).

Providers report expected operational conditions - exhausted credit, rate
limits, bad keys, oversized requests - as raw HTTP errors with JSON bodies.
Surfacing those verbatim makes a routine, fixable situation look like a
crash. This module recognises the common cases from the provider's own error
text and renders a concise explanation plus concrete next steps, while always
preserving the original message for debugging.

Classification is text-based on purpose: it works across providers without
coupling the CLI to any SDK's exception hierarchy, and an unrecognised error
degrades to the original text rather than being swallowed.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderDiagnosis:
    """A recognised provider failure, explained."""

    reason: str
    actions: tuple[str, ...]

    def render(self, provider_error: str) -> str:
        lines = [f"Reason: {self.reason}", "", "Suggested actions:"]
        lines += [f"  - {action}" for action in self.actions]
        lines += ["", f"Provider error: {provider_error}"]
        return "\n".join(lines)


_INSUFFICIENT_CREDIT = ProviderDiagnosis(
    reason="The provider account has insufficient credit for the requested token limit.",
    actions=(
        "Reduce max_output_tokens in qaops.yaml",
        "Top up your provider credits",
        "Switch to a cheaper model or another provider",
    ),
)

_RATE_LIMITED = ProviderDiagnosis(
    reason="The provider is rate-limiting requests, often on a free tier.",
    actions=(
        "Wait a few minutes and retry",
        "Switch to a different model or provider",
        "Add your own upstream API key to raise the limit",
    ),
)

_AUTH_FAILED = ProviderDiagnosis(
    reason="The provider rejected the API key.",
    actions=(
        "Check the API key environment variable is set in this shell",
        "Verify the key is valid and not expired",
        "Confirm the key matches the configured provider",
    ),
)

_CONTEXT_EXCEEDED = ProviderDiagnosis(
    reason="The request exceeded the model's token limit.",
    actions=(
        "Reduce max_output_tokens in qaops.yaml",
        "Lower chunk_safety_margin so chunks are smaller",
        "Use a model with a larger limit",
    ),
)

_MODEL_UNAVAILABLE = ProviderDiagnosis(
    reason="The configured model is unavailable or unknown to the provider.",
    actions=(
        "Check the model identifier in qaops.yaml",
        "Confirm the model is still offered by the provider",
        "Select a different model",
    ),
)

# Matched in order; first hit wins. Lowercased substrings of the provider's
# own message. Ordered so more specific phrases are tested before generic ones.
_PATTERNS: tuple[tuple[tuple[str, ...], ProviderDiagnosis], ...] = (
    (
        ("more credits", "insufficient credit", "insufficient_quota", "can only afford"),
        _INSUFFICIENT_CREDIT,
    ),
    (("rate-limited", "rate limited", "rate_limit", "429", "too many requests"), _RATE_LIMITED),
    (
        ("invalid x-api-key", "authentication", "unauthorized", "401", "invalid api key"),
        _AUTH_FAILED,
    ),
    (
        ("context length", "maximum context", "context_length_exceeded", "too many tokens"),
        _CONTEXT_EXCEEDED,
    ),
    (("model is unavailable", "unknown model", "no endpoints found", "404"), _MODEL_UNAVAILABLE),
)


def diagnose_provider_error(message: str) -> ProviderDiagnosis | None:
    """Classify a provider error message, or None if unrecognised."""
    lowered = message.casefold()
    for needles, diagnosis in _PATTERNS:
        if any(needle in lowered for needle in needles):
            return diagnosis
    return None
