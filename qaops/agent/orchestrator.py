"""OrchestratorAgent - backward-compatible facade (ADR-043, Phase 28).

Phase 26 introduced OrchestratorAgent as a monolith; Phase 28 decomposed its
responsibilities into a SupervisorAgent coordinating three specialized agents.
To preserve backward compatibility, OrchestratorAgent is KEPT as a thin facade
that delegates every operation to the supervisor. Its public API - name, plan,
reflect, execute, execute_until_goal - is unchanged, so existing callers and
tests continue to work exactly as before.

The facade adds no behaviour of its own; it exists only so that code written
against the Phase 26/27 OrchestratorAgent keeps working. New code may talk to
SupervisorAgent directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qaops.agent.base import Agent
from qaops.agent.supervisor import SupervisorAgent

if TYPE_CHECKING:
    from pathlib import Path

    from qaops.agent.models import ExecutionPlan, LoopSummary, Reflection
    from qaops.config import QAOpsSettings
    from qaops.llm import LLMClient, PromptLoader
    from qaops.services.design_service import DesignOutcome, DesignService


class OrchestratorAgent(Agent):
    """Thin facade delegating to SupervisorAgent (kept for compatibility)."""

    def __init__(
        self,
        service: DesignService,
        *,
        client: LLMClient | None = None,
        prompts: PromptLoader | None = None,
        settings: QAOpsSettings | None = None,
    ) -> None:
        self._supervisor = SupervisorAgent(
            service, client=client, prompts=prompts, settings=settings
        )

    @property
    def name(self) -> str:
        return "orchestrator"

    def plan(
        self,
        input_path: Path,
        settings: QAOpsSettings,
        *,
        goal: str = "Generate a complete test-design pack.",
        from_: str | None = None,
    ) -> ExecutionPlan:
        return self._supervisor.plan(input_path, settings, goal=goal, from_=from_)

    def reflect(
        self,
        outcome: DesignOutcome | None,
        settings: QAOpsSettings,
        *,
        failed_stage: str | None = None,
        skipped_stages: list[str] | None = None,
        attempt_history: list[dict[str, object]] | None = None,
    ) -> Reflection:
        return self._supervisor.reflect(
            outcome,
            settings,
            failed_stage=failed_stage,
            skipped_stages=skipped_stages,
            attempt_history=attempt_history,
        )

    def execute(
        self,
        input_path: Path,
        settings: QAOpsSettings,
        *,
        goal: str = "Generate a complete test-design pack.",
        from_: str | None = None,
        report: object | None = None,
    ) -> tuple[ExecutionPlan, DesignOutcome, Reflection]:
        return self._supervisor.execute(input_path, settings, goal=goal, from_=from_, report=report)

    def execute_until_goal(
        self,
        input_path: Path,
        settings: QAOpsSettings,
        *,
        goal: str = "Generate a complete test-design pack.",
        from_: str | None = None,
        report: object | None = None,
        events: object | None = None,
    ) -> tuple[ExecutionPlan, DesignOutcome | None, LoopSummary]:
        return self._supervisor.execute_until_goal(
            input_path, settings, goal=goal, from_=from_, report=report, events=events
        )
