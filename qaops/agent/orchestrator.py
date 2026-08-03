"""The Orchestrator Agent - QAOps' first agent (ADR-041, Phase 26).

It reasons about HOW the deterministic pipeline executes: it builds an execution
plan, decides resume-vs-restart, delegates the actual execution to the unchanged
DesignService, and produces a post-run reflection. It NEVER generates or mutates
any pipeline artifact - requirements, business rules, gaps, scenarios,
conditions, test cases, and coverage remain owned by the deterministic stages.

Determinism guarantee: execution is performed by DesignService.run() / .resume()
exactly as in Phase 25. The agent only chooses WHICH of those to call and
enriches the surrounding reasoning. If the agent makes no structural
intervention (no checkpoints -> full run), behaviour is identical to Phase 25.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qaops.agent.base import Agent
from qaops.agent.planner import ExecutionPlanner
from qaops.agent.reflection import Reflector
from qaops.execution.checkpoint import CheckpointStore

if TYPE_CHECKING:
    from pathlib import Path

    from qaops.agent.models import ExecutionPlan, Reflection
    from qaops.config import QAOpsSettings
    from qaops.llm import LLMClient, PromptLoader
    from qaops.services.design_service import DesignOutcome, DesignService


class OrchestratorAgent(Agent):
    """Plans, delegates execution to the pipeline, and reflects."""

    def __init__(
        self,
        service: DesignService,
        *,
        client: LLMClient | None = None,
        prompts: PromptLoader | None = None,
        settings: QAOpsSettings | None = None,
    ) -> None:
        self._service = service
        self._planner = ExecutionPlanner(client=client, prompts=prompts, settings=settings)
        self._reflector = Reflector(client=client, prompts=prompts, settings=settings)

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
        """Produce an execution plan without running anything."""
        entry_point, _ = self._service.resolve_entry_point(input_path, from_)
        checkpoints = CheckpointStore(settings.output_dir)
        return self._planner.build(goal, entry_point, checkpoints)

    def reflect(
        self,
        outcome: DesignOutcome | None,
        settings: QAOpsSettings,
        *,
        failed_stage: str | None = None,
        skipped_stages: list[str] | None = None,
        attempt_history: list[dict[str, object]] | None = None,
    ) -> Reflection:
        """Produce a post-execution reflection (reasoning only)."""
        checkpoints = CheckpointStore(settings.output_dir)
        result = outcome.result if outcome is not None else None
        return self._reflector.build(
            result=result,
            checkpoints=checkpoints,
            attempt_history=attempt_history,
            failed_stage=failed_stage,
            skipped_stages=skipped_stages,
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
        """Plan, execute via the deterministic pipeline, and reflect.

        Execution is delegated to DesignService.resume() when the plan chose to
        resume (checkpoints exist) and DesignService.run() otherwise - the exact
        Phase-25 entry points, unchanged. The agent adds the plan and reflection
        around them; it never runs a stage itself.
        """
        plan = self.plan(input_path, settings, goal=goal, from_=from_)

        reporter = report if callable(report) else None
        if plan.resume:
            outcome = self._service.resume(input_path, settings, from_=from_, report=reporter)
        else:
            outcome = self._service.run(input_path, settings, from_=from_, report=reporter)

        skipped = [s.stage for s in plan.steps if s.status.value == "reuse"]
        reflection = self.reflect(outcome, settings, skipped_stages=skipped)
        return plan, outcome, reflection
