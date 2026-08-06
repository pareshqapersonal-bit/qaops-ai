"""ReflectionAgent - owns post-execution reflection (ADR-043, Phase 28).

Wraps the existing Reflector without changing it. Produces the Reflection
(successes/failures/recovered/lessons/recommendations and the Phase-27 terminal
signals) from the produced result and execution metadata. Reasoning only: it
reads the result and never regenerates any artifact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qaops.agent.base import Agent
from qaops.agent.reflection import Reflector
from qaops.execution.checkpoint import CheckpointStore

if TYPE_CHECKING:
    from qaops.agent.models import Reflection
    from qaops.config import QAOpsSettings
    from qaops.llm import LLMClient, PromptLoader
    from qaops.services.design_service import DesignOutcome


class ReflectionAgent(Agent):
    """Builds the reflection by delegating to Reflector."""

    def __init__(
        self,
        *,
        client: LLMClient | None = None,
        prompts: PromptLoader | None = None,
        settings: QAOpsSettings | None = None,
    ) -> None:
        self._reflector = Reflector(client=client, prompts=prompts, settings=settings)

    @property
    def name(self) -> str:
        return "reflection"

    @property
    def reflector(self) -> Reflector:
        """The underlying Reflector, for the loop engine which the supervisor
        passes it to (GoalDrivenLoop builds the cumulative reflection itself)."""
        return self._reflector

    def reflect(
        self,
        outcome: DesignOutcome | None,
        settings: QAOpsSettings,
        *,
        failed_stage: str | None = None,
        skipped_stages: list[str] | None = None,
        attempt_history: list[dict[str, object]] | None = None,
    ) -> Reflection:
        checkpoints = CheckpointStore(settings.output_dir)
        result = outcome.result if outcome is not None else None
        return self._reflector.build(
            result=result,
            checkpoints=checkpoints,
            attempt_history=attempt_history,
            failed_stage=failed_stage,
            skipped_stages=skipped_stages,
        )
