"""Base class for QAOps agents (ADR-041, Phase 26).

QAOps' first agent is the OrchestratorAgent. This base establishes the shape all
future agents share so adding another agent does not require a refactor:

  * an agent has a stable `name`;
  * an agent may reason with an LLM, but reasoning is ADVISORY - an agent never
    owns or mutates deterministic pipeline artifacts;
  * an agent degrades safely: if the LLM is unavailable or returns unusable
    output, the agent falls back to deterministic behaviour rather than failing
    the run.

Deliberately minimal. It is an extension point, not a framework.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Agent(ABC):
    """A QAOps agent: reasons about execution, never generates artifacts.

    Subclasses implement domain behaviour (the orchestrator plans and reflects).
    The contract every agent upholds: it may DECIDE and EXPLAIN, but the
    deterministic pipeline remains the sole author of requirements, business
    rules, gaps, scenarios, conditions, test cases, and coverage.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier for logs, responses, and future routing."""
        ...
