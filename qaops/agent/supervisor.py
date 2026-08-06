"""SupervisorAgent - coordinates the specialized agents (ADR-043, Phase 28).

The supervisor is the coordination layer the runner talks to. It composes the
three specialized agents and drives the GoalDrivenLoop engine:

    Runner -> SupervisorAgent -> {PlanningAgent, ExecutionAgent, ReflectionAgent}
                                       -> GoalDrivenLoop (observe/decide/act)

Responsibilities (and only these):
  * PlanningAgent owns planning;
  * ExecutionAgent delegates acts to DesignService;
  * ReflectionAgent owns reflection;
  * GoalDrivenLoop owns the execution cycle;
  * the supervisor wires them together and exposes the same operations the
    monolithic OrchestratorAgent did.

This is a pure structural refactor: the supervisor produces the identical
(plan, outcome, LoopSummary) / (plan, outcome, Reflection) results Phase 27
produced, because every collaborator is the same underlying logic, only re-homed.
The supervisor generates no artifact, executes no stage, and writes no checkpoint.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qaops.agent.agents import ExecutionAgent, PlanningAgent, ReflectionAgent
from qaops.agent.base import Agent
from qaops.agent.loop import GoalDrivenLoop

if TYPE_CHECKING:
    from pathlib import Path

    from qaops.agent.models import ExecutionPlan, LoopSummary, Reflection
    from qaops.config import QAOpsSettings
    from qaops.llm import LLMClient, PromptLoader
    from qaops.services.design_service import DesignOutcome, DesignService


class SupervisorAgent(Agent):
    """Coordinates the planning, execution, and reflection agents."""

    def __init__(
        self,
        service: DesignService,
        *,
        client: LLMClient | None = None,
        prompts: PromptLoader | None = None,
        settings: QAOpsSettings | None = None,
    ) -> None:
        self._planning = PlanningAgent(service, client=client, prompts=prompts, settings=settings)
        self._execution = ExecutionAgent(service)
        self._reflection = ReflectionAgent(client=client, prompts=prompts, settings=settings)

    @property
    def name(self) -> str:
        return "supervisor"

    # -- planning ----------------------------------------------------------

    def plan(
        self,
        input_path: Path,
        settings: QAOpsSettings,
        *,
        goal: str = "Generate a complete test-design pack.",
        from_: str | None = None,
    ) -> ExecutionPlan:
        return self._planning.plan(input_path, settings, goal=goal, from_=from_)

    # -- reflection --------------------------------------------------------

    def reflect(
        self,
        outcome: DesignOutcome | None,
        settings: QAOpsSettings,
        *,
        failed_stage: str | None = None,
        skipped_stages: list[str] | None = None,
        attempt_history: list[dict[str, object]] | None = None,
    ) -> Reflection:
        return self._reflection.reflect(
            outcome,
            settings,
            failed_stage=failed_stage,
            skipped_stages=skipped_stages,
            attempt_history=attempt_history,
        )

    # -- single-shot execution (Phase 26 semantics) ------------------------

    def execute(
        self,
        input_path: Path,
        settings: QAOpsSettings,
        *,
        goal: str = "Generate a complete test-design pack.",
        from_: str | None = None,
        report: object | None = None,
    ) -> tuple[ExecutionPlan, DesignOutcome, Reflection]:
        plan = self.plan(input_path, settings, goal=goal, from_=from_)
        reporter = report if callable(report) else None
        if plan.resume:
            outcome = self._execution.resume(input_path, settings, from_=from_, report=reporter)
        else:
            outcome = self._execution.run(input_path, settings, from_=from_, report=reporter)
        skipped = [s.stage for s in plan.steps if s.status.value == "reuse"]
        reflection = self.reflect(outcome, settings, skipped_stages=skipped)
        return plan, outcome, reflection

    # -- goal-driven execution (Phase 27 semantics) ------------------------

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
        plan = self.plan(input_path, settings, goal=goal, from_=from_)
        reporter = report if callable(report) else None
        event_sink = events if callable(events) else None
        loop = GoalDrivenLoop(self._execution, self._reflection.reflector)
        outcome, summary = loop.run(
            input_path,
            settings,
            goal=goal,
            from_=from_,
            report=reporter,
            events=event_sink,
        )
        return plan, outcome, summary
