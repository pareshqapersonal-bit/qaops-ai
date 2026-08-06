"""PlanningAgent - owns execution planning (ADR-043, Phase 28).

Wraps the existing ExecutionPlanner without changing it. Produces the
ExecutionPlan (which stages run, resume vs restart, the recorded decisions) from
the entry point and checkpoint state. Reasoning only; it neither executes a stage
nor generates an artifact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qaops.agent.base import Agent
from qaops.agent.planner import ExecutionPlanner
from qaops.execution.checkpoint import CheckpointStore

if TYPE_CHECKING:
    from pathlib import Path

    from qaops.agent.models import ExecutionPlan
    from qaops.config import QAOpsSettings
    from qaops.llm import LLMClient, PromptLoader
    from qaops.services.design_service import DesignService


class PlanningAgent(Agent):
    """Builds the execution plan by delegating to ExecutionPlanner."""

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

    @property
    def name(self) -> str:
        return "planning"

    def plan(
        self,
        input_path: Path,
        settings: QAOpsSettings,
        *,
        goal: str,
        from_: str | None = None,
    ) -> ExecutionPlan:
        entry_point, _ = self._service.resolve_entry_point(input_path, from_)
        checkpoints = CheckpointStore(settings.output_dir)
        return self._planner.build(goal, entry_point, checkpoints)
