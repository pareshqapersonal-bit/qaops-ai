"""The goal-driven execution loop (ADR-042, Phase 27).

Evolves the Phase-26 single-shot plan->act->reflect into a bounded
observe -> decide -> act -> observe -> ... -> reflect loop. The loop manages
execution until a terminal condition, but it NEVER executes a stage, generates an
artifact, or writes a checkpoint. Every act is a delegated call to
DesignService.run()/.resume(); every observation is a read.

Terminal conditions:
  * COMPLETED               - a run/resume produced a full result;
  * MAX_RESUME_ATTEMPTS     - settings.max_resume_attempts reached;
  * NEEDS_CLARIFICATION     - ambiguity over threshold (unresolved/gaps);
  * NEEDS_MANUAL_REVIEW     - the same stage failed across resume attempts.

Determinism: when the first act succeeds (the common case, and always when there
are no checkpoints), the loop runs exactly one iteration and returns the same
outcome Phase 26 would - byte-identical artifacts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qaops.agent.models import (
    Decision,
    LoopDecision,
    LoopIteration,
    LoopSummary,
    Observation,
    TerminalReason,
)
from qaops.agent.observe import observe
from qaops.agent.reflection import (
    _GAP_COUNT_THRESHOLD,
    _UNRESOLVED_FRACTION_THRESHOLD,
)
from qaops.core.errors import StageError
from qaops.execution.checkpoint import CheckpointStore

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from qaops.agent.reflection import Reflector
    from qaops.config import QAOpsSettings
    from qaops.services.design_service import DesignOutcome, DesignService


def _clarification_needed(obs: Observation) -> bool:
    if obs.total_conditions > 0 and (
        obs.unresolved_conditions / obs.total_conditions >= _UNRESOLVED_FRACTION_THRESHOLD
    ):
        return True
    return obs.gap_count >= _GAP_COUNT_THRESHOLD


def decide(obs: Observation, *, max_resume_attempts: int) -> tuple[LoopDecision, Decision]:
    """Choose the next action from a read-only Observation (ADR-042).

    Deterministic. Returns the loop's control decision plus the structured
    Decision record (decision / reason / alternative / rejected-because).
    """
    if obs.succeeded:
        if _clarification_needed(obs):
            return (
                LoopDecision.RECOMMEND_CLARIFICATION,
                Decision(
                    decision="Finish, but recommend clarification.",
                    reason=(
                        f"The run completed, but {obs.unresolved_conditions} of "
                        f"{obs.total_conditions} conditions are unresolved and "
                        f"{obs.gap_count} gaps are open."
                    ),
                    alternative_considered="Treat the pack as final.",
                    rejected_because="Ambiguity above threshold undermines confidence.",
                ),
            )
        return (
            LoopDecision.CONTINUE,
            Decision(
                decision="Finish: the pipeline completed successfully.",
                reason="A run/resume produced a complete result with acceptable ambiguity.",
                alternative_considered="Continue resuming.",
                rejected_because="There is nothing left to execute.",
            ),
        )

    # The last act failed. Decide whether resuming again is worthwhile.
    if obs.repeated_failure:
        return (
            LoopDecision.RECOMMEND_MANUAL_REVIEW,
            Decision(
                decision=f"Stop and recommend manual review of {obs.failed_stage}.",
                reason=f"Stage {obs.failed_stage} has failed on more than one attempt.",
                alternative_considered="Resume again from the checkpoint.",
                rejected_because="Repeated failure at the same stage is unlikely to self-resolve.",
            ),
        )
    if obs.resume_attempts >= max_resume_attempts:
        return (
            LoopDecision.STOP,
            Decision(
                decision="Stop: the resume-attempt limit was reached.",
                reason=f"Reached max_resume_attempts={max_resume_attempts}.",
                alternative_considered="Resume once more.",
                rejected_because="Exceeding the configured bound risks looping without progress.",
            ),
        )
    if not obs.completed_stages:
        return (
            LoopDecision.STOP,
            Decision(
                decision="Stop: nothing to resume from.",
                reason="No stage completed, so there is no checkpoint to resume.",
                alternative_considered="Resume from a checkpoint.",
                rejected_because="No completed checkpoint exists.",
            ),
        )
    return (
        LoopDecision.RESUME,
        Decision(
            decision=f"Resume from the checkpoint after {obs.completed_stages[-1]}.",
            reason=f"{len(obs.completed_stages)} stage(s) completed before the failure.",
            alternative_considered="Restart from the beginning.",
            rejected_because="Restarting would waste completed work.",
        ),
    )


class GoalDrivenLoop:
    """Runs the observe->decide->act loop, delegating every act to DesignService."""

    def __init__(self, service: DesignService, reflector: Reflector) -> None:
        self._service = service
        self._reflector = reflector

    def run(
        self,
        input_path: Path,
        settings: QAOpsSettings,
        *,
        goal: str,
        from_: str | None = None,
        report: Callable[[str], None] | None = None,
        events: Callable[[object], None] | None = None,
    ) -> tuple[DesignOutcome | None, LoopSummary]:
        checkpoints = CheckpointStore(settings.output_dir)
        iterations: list[LoopIteration] = []
        prior_failed: list[str] = []
        resume_attempts = 0
        iteration = 0
        outcome: DesignOutcome | None = None
        terminal: TerminalReason = TerminalReason.COMPLETED
        last_error: str | None = None
        last_failed_stage: str | None = None
        last_attempts: list[dict[str, object]] = []

        # First act: run (or resume if checkpoints already exist for this run).
        act_is_resume = bool(checkpoints.completed_stages())

        while True:
            iteration += 1
            failed_stage: str | None = None
            attempts: list[dict[str, object]] = []
            try:
                if act_is_resume:
                    outcome = self._service.resume(
                        input_path, settings, from_=from_, report=report, events=events
                    )
                else:
                    outcome = self._service.run(
                        input_path, settings, from_=from_, report=report, events=events
                    )
            except StageError as exc:
                outcome = None
                failed_stage = exc.stage_name
                attempts = list(exc.attempts)
                last_error = str(exc)
                last_failed_stage = exc.stage_name
                last_attempts = attempts

            obs = observe(
                iteration=iteration,
                resume_attempts=resume_attempts,
                checkpoints=checkpoints,
                result=outcome.result if outcome is not None else None,
                failed_stage=failed_stage,
                prior_failed_stages=prior_failed,
            )
            loop_decision, decision = decide(obs, max_resume_attempts=settings.max_resume_attempts)
            will_act = loop_decision is LoopDecision.RESUME
            iterations.append(
                LoopIteration(
                    iteration=iteration, observation=obs, decision=decision, acted=will_act
                )
            )

            if loop_decision is LoopDecision.RESUME:
                if failed_stage:
                    prior_failed.append(failed_stage)
                resume_attempts += 1
                act_is_resume = True
                continue

            terminal = {
                LoopDecision.CONTINUE: TerminalReason.COMPLETED,
                LoopDecision.RECOMMEND_CLARIFICATION: TerminalReason.NEEDS_CLARIFICATION,
                LoopDecision.RECOMMEND_MANUAL_REVIEW: TerminalReason.NEEDS_MANUAL_REVIEW,
                LoopDecision.STOP: (
                    TerminalReason.MAX_RESUME_ATTEMPTS
                    if resume_attempts >= settings.max_resume_attempts and resume_attempts > 0
                    else TerminalReason.NEEDS_MANUAL_REVIEW
                ),
            }[loop_decision]
            break

        reflection = self._reflector.build(
            result=outcome.result if outcome is not None else None,
            checkpoints=checkpoints,
            attempt_history=attempts,
            failed_stage=failed_stage,
            skipped_stages=checkpoints.completed_stages() if act_is_resume else None,
        )
        reflection.goal_achieved = terminal is TerminalReason.COMPLETED
        reflection.needs_clarification = terminal is TerminalReason.NEEDS_CLARIFICATION
        reflection.needs_manual_review = terminal in (
            TerminalReason.NEEDS_MANUAL_REVIEW,
            TerminalReason.MAX_RESUME_ATTEMPTS,
        )

        summary = LoopSummary(
            goal=goal,
            iterations=iterations,
            terminal_reason=terminal.value,
            resume_attempts=resume_attempts,
            reflection=reflection,
            last_error=last_error,
            last_failed_stage=last_failed_stage,
            last_attempts=last_attempts,
        )
        return outcome, summary
