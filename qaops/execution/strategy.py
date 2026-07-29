"""Execution strategy: which candidates are eligible for a run (ADR-034).

Free/paid eligibility cannot live at provider level alone: OpenRouter and Gemini
each expose both free and paid models, so eligibility is a property of the
CANDIDATE (a provider+model pair), carried by ``ModelInfo.free``. This module
adds only the strategy that decides how that per-candidate flag is USED:

- ANY        - unrestricted; the default, identical to prior behaviour.
- FREE_FIRST - free-eligible candidates are exhausted before paid ones.
- FREE_ONLY  - only free-eligible candidates; paid providers/models are never
               invoked (so Anthropic, which has no free candidate, is skipped
               entirely).

The strategy is turned into a concrete filter/ordering by the selector and the
executor; nothing here imports them, keeping the dependency direction clean.
"""

from enum import StrEnum


class ExecutionStrategy(StrEnum):
    """How free-eligibility constrains candidate selection for a run."""

    ANY = "any"
    FREE_FIRST = "free_first"
    FREE_ONLY = "free_only"

    @property
    def requires_free(self) -> bool:
        """True when only free-eligible candidates may be used at all."""
        return self is ExecutionStrategy.FREE_ONLY

    @property
    def prefers_free(self) -> bool:
        """True when free-eligible candidates should be tried before paid."""
        return self in {ExecutionStrategy.FREE_FIRST, ExecutionStrategy.FREE_ONLY}


def parse_strategy(value: str) -> ExecutionStrategy:
    """Parse a settings string into a strategy, defaulting safely to ANY."""
    try:
        return ExecutionStrategy(value.strip().casefold())
    except ValueError:
        return ExecutionStrategy.ANY
