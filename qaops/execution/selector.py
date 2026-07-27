"""Bounded, ranked model candidate selection (ADR-029).

Live discovery can surface hundreds of models for a provider. Letting the
executor walk that whole catalogue is what caused a credit-exhausted account to
produce hundreds of attempts. This layer sits between discovery and execution:
it filters models that cannot serve the stage, ranks the rest deterministically,
and returns a bounded pool.

The selector reuses the registry's capability metadata and adds no discovery of
its own. It is pure and deterministic - the same inputs always yield the same
ordered pool - so execution is predictable and testable.
"""

from dataclasses import dataclass

from qaops.execution.models import ModelInfo

# Signals whose weights define the ranking. Higher total score ranks earlier.
# Ordering is by (-score, name) so equal scores fall back to a stable
# alphabetical order - deterministic for equal candidates (requirement 4).
_SCORE_CONFIGURED = 1000  # the model the user configured leads, always
_SCORE_STRUCTURED = 100  # known structured-output support
_SCORE_CONTEXT_HEADROOM = 40  # comfortably fits the stage's context need
_SCORE_OUTPUT_HEADROOM = 40  # comfortably fits the stage's output need
_SCORE_PRIORITY_BASE = 60  # curated priority; lower priority number ranks up
_SCORE_FREE = 5  # a mild nudge toward free models, all else equal


@dataclass(frozen=True)
class StageRequirements:
    """What a stage needs from a model.

    Defaults describe every current QAOps stage: they all emit structured JSON,
    so structured output is always required. Context and output needs are
    derived from the input size where known, else left at zero (no constraint).
    """

    needs_structured_output: bool = True
    min_context_chars: int = 0
    min_output_chars: int = 0


@dataclass(frozen=True)
class ScoredModel:
    """A candidate with its score and a short human-readable rationale."""

    model: ModelInfo
    score: int
    reason: str


def _passes_filter(
    model: ModelInfo, requirements: StageRequirements, excluded: set[str]
) -> tuple[bool, str]:
    """Whether a model can serve the stage at all, and why not if it cannot."""
    if model.name in excluded:
        return False, "excluded after a prior failure this run"
    if requirements.needs_structured_output and not model.structured_output:
        return False, "no structured-output support"
    if model.max_context_chars < requirements.min_context_chars:
        return False, "context window too small for the input"
    if model.max_output_chars < requirements.min_output_chars:
        return False, "output capacity too small for the stage"
    return True, "compatible"


def _score(model: ModelInfo, requirements: StageRequirements, configured: str | None) -> int:
    """Deterministic score from capability signals. Higher ranks earlier."""
    score = 0
    if configured is not None and model.name == configured:
        score += _SCORE_CONFIGURED
    if model.structured_output:
        score += _SCORE_STRUCTURED
    # Priority is "lower is better" in the registry (10 beats 40); convert to a
    # positive contribution capped so it never outweighs a capability signal.
    score += max(0, _SCORE_PRIORITY_BASE - model.priority)
    if (
        requirements.min_context_chars
        and model.max_context_chars >= 2 * requirements.min_context_chars
    ):
        score += _SCORE_CONTEXT_HEADROOM
    if (
        requirements.min_output_chars
        and model.max_output_chars >= 2 * requirements.min_output_chars
    ):
        score += _SCORE_OUTPUT_HEADROOM
    if model.free:
        score += _SCORE_FREE
    return score


def select_candidates(
    models: list[ModelInfo],
    requirements: StageRequirements,
    *,
    limit: int,
    configured: str | None = None,
    excluded: set[str] | None = None,
) -> list[ScoredModel]:
    """Filter, rank, and bound a discovered model list.

    Returns at most `limit` compatible models, best first, each with a reason.
    Incompatible models are dropped (not returned). Ordering is by descending
    score then model name, so equal candidates have a stable, deterministic
    order regardless of discovery order.
    """
    excluded_set = excluded or set()
    compatible: list[ScoredModel] = []
    for model in models:
        ok, reason = _passes_filter(model, requirements, excluded_set)
        if not ok:
            continue
        score = _score(model, requirements, configured)
        compatible.append(ScoredModel(model=model, score=score, reason=reason))

    compatible.sort(key=lambda scored: (-scored.score, scored.model.name))
    return compatible[: max(1, limit)]


def rejection_reasons(
    models: list[ModelInfo],
    requirements: StageRequirements,
    *,
    excluded: set[str] | None = None,
) -> dict[str, str]:
    """Explain why each rejected model was filtered out (requirement 5).

    Diagnostic only - not used in the hot path, but available for a `--explain`
    style view or a test that asserts filtering behaviour.
    """
    excluded_set = excluded or set()
    reasons: dict[str, str] = {}
    for model in models:
        ok, reason = _passes_filter(model, requirements, excluded_set)
        if not ok:
            reasons[model.name] = reason
    return reasons
