"""ExecutionAgent - delegates one act to the deterministic pipeline (ADR-043).

The ExecutionAgent performs execution acts, but NEVER runs a pipeline stage
itself: it delegates only to DesignService.run() and DesignService.resume() - the
same entry points Phases 25-27 used. It is the single place the agent layer
touches execution, which keeps the "agent never executes a stage" guarantee
easy to audit. It reads nothing it should not and writes no checkpoint.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qaops.agent.base import Agent

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from qaops.config import QAOpsSettings
    from qaops.execution.events import EventSink
    from qaops.services.design_service import DesignOutcome, DesignService


class ExecutionAgent(Agent):
    """Delegates run/resume acts to DesignService; never executes a stage."""

    def __init__(self, service: DesignService) -> None:
        self._service = service

    @property
    def name(self) -> str:
        return "execution"

    def run(
        self,
        input_path: Path,
        settings: QAOpsSettings,
        *,
        from_: str | None = None,
        report: Callable[[str], None] | None = None,
        events: EventSink | None = None,
    ) -> DesignOutcome:
        """Delegate a fresh run to the pipeline (DesignService.run)."""
        return self._service.run(input_path, settings, from_=from_, report=report, events=events)

    def resume(
        self,
        input_path: Path,
        settings: QAOpsSettings,
        *,
        from_: str | None = None,
        report: Callable[[str], None] | None = None,
        events: EventSink | None = None,
    ) -> DesignOutcome:
        """Delegate a resume to the pipeline (DesignService.resume)."""
        return self._service.resume(input_path, settings, from_=from_, report=report, events=events)
