"""Read-only observation for the goal-driven loop (ADR-042, Phase 27).

`observe()` snapshots the execution signals the agent decides on - all read from
existing surfaces (CheckpointStore, the last outcome/error, coverage metrics). It
NEVER writes a checkpoint, mutates a stage output, or touches execution state.
The Observation it returns is the sole input to a Decision.

Determining "repeated failure": a stage counts as repeatedly failing when it is
the failed stage AND it has already failed on a previous iteration of this loop
(tracked by the caller and passed in). That signal drives the
recommend-manual-review decision - stop rather than resume the same stage forever.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qaops.agent.models import Observation

if TYPE_CHECKING:
    from qaops.execution.checkpoint import CheckpointStore
    from qaops.models import TestDesignResult


def observe(
    *,
    iteration: int,
    resume_attempts: int,
    checkpoints: CheckpointStore,
    result: TestDesignResult | None,
    failed_stage: str | None,
    prior_failed_stages: list[str],
) -> Observation:
    """Build a read-only Observation from current execution state."""
    completed = checkpoints.completed_stages()
    repeated = bool(failed_stage) and failed_stage in prior_failed_stages

    unresolved = 0
    total = 0
    gaps = 0
    if result is not None:
        metrics = result.coverage.metrics
        unresolved = metrics.unresolved_conditions
        total = metrics.total_conditions
        gaps = len(result.gap_report.gaps)

    return Observation(
        iteration=iteration,
        resume_attempts=resume_attempts,
        succeeded=result is not None and failed_stage is None,
        completed_stages=completed,
        failed_stage=failed_stage,
        repeated_failure=repeated,
        unresolved_conditions=unresolved,
        total_conditions=total,
        gap_count=gaps,
    )
