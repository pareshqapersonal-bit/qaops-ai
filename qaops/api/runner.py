"""Background execution of a design run (ADR-028).

Bridges the run store and the DesignService. The API schedules `execute_run`
on a background thread and returns immediately; this function moves the run
through running -> completed | failed, capturing progress and a safe error
representation.

"Safe" matters: a provider failure can carry an API key or authorization header
in its text. The error stored on a run is the exception's message only, and the
API never returns tracebacks. Provider-specific secret redaction happens in
`_safe_error`.
"""

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from qaops.agent import SupervisorAgent
from qaops.api.runs import ArtifactMeta, RunProgress, RunStatus, RunStore
from qaops.config import QAOpsSettings
from qaops.core.errors import QAOpsError, StageError
from qaops.execution.events import EventType, ExecutionEvent, render_line
from qaops.services import DesignService, summarize

logger = logging.getLogger(__name__)

# Patterns whose matches are replaced before an error reaches a client, so a
# leaked key or header never appears in an API response or log line.
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]+"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[A-Za-z0-9._-]+"),
    re.compile(r"(?i)(x-api-key\s*[=:]\s*)[A-Za-z0-9._-]+"),
)


def _redact(message: str) -> str:
    """Redact any secrets from a message string (client-safe)."""
    for pattern in _SECRET_PATTERNS:
        message = pattern.sub("[redacted]", message)
    return message


def _safe_error(exc: Exception) -> str:
    """A client-safe message: the exception text with any secrets redacted."""
    return _redact(str(exc) or exc.__class__.__name__)


def _build_review(
    result: object, output_dir: Path
) -> tuple[dict[str, object] | None, ArtifactMeta | None]:
    """Run the deterministic QualityReviewer and export its ReviewReport.

    Phase 30 (ADR-045). Invoked by the runner ONLY on a COMPLETED run, AFTER the
    supervisor returns, so the Phase 28 supervisor architecture is unchanged. The
    reviewer is read-only and advisory: it consumes the finished result (and its
    CoverageReport) and never mutates it, invokes no stage, and writes no
    checkpoint. Its output is surfaced additively - a plain-dict payload for the
    run status plus a standalone JSON export - and any failure here degrades to
    "no review" without affecting the COMPLETED status (mirrors how reflection and
    loop_summary serialization already degrade).

    Returns (review_payload, review_artifact_meta); either may be None on failure.
    """
    from qaops.models import TestDesignResult
    from qaops.review import QualityReviewer

    if not isinstance(result, TestDesignResult):
        return None, None
    try:
        report = QualityReviewer().review(result)
    except Exception:  # pragma: no cover - advisory; never fail the run
        logger.info("api.quality_review_failed")
        return None, None

    payload = report.model_dump(mode="json")
    artifact: ArtifactMeta | None = None
    try:
        target = output_dir / "review_report.json"
        target.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        artifact = ArtifactMeta(name=target.name, format="JSON (review)", path=target)
    except OSError:  # pragma: no cover - export is best-effort
        logger.info("api.quality_review_export_failed")
    return payload, artifact


def _collect_partial_artifacts(output_dir: Path) -> list[ArtifactMeta]:
    """Gather report files a failed run left in its workspace (ADR-040).

    The service writes partial CSV-bundle and partial JSON files on failure;
    this discovers them so the API can offer them for download. The checkpoint
    directory itself is internal and never listed.
    """
    artifacts: list[ArtifactMeta] = []
    if not output_dir.exists():
        return artifacts
    for path in sorted(output_dir.iterdir()):
        if path.is_dir():
            continue
        suffix = path.suffix.lower()
        if suffix == ".csv":
            fmt = "CSV (partial)"
        elif suffix == ".json":
            fmt = "JSON (partial)"
        elif suffix == ".md":
            fmt = "Markdown (partial)"
        else:
            continue
        artifacts.append(ArtifactMeta(name=path.name, format=fmt, path=path))
    return artifacts


def resume_run(
    store: RunStore,
    run_id: str,
    settings: QAOpsSettings,
    service: DesignService,
) -> None:
    """Resume a resumable run from its last checkpoint (ADR-040).

    Reuses completed stages via the workspace checkpoints and runs only the
    remaining stages. On success the run becomes COMPLETED with full artifacts;
    on another failure it stays PARTIALLY_COMPLETED/resumable.
    """
    run = store.get(run_id)
    if run is None:  # pragma: no cover
        return
    input_files = list(run.input_dir.iterdir())
    if not input_files:  # pragma: no cover
        store.update(run_id, status=RunStatus.FAILED, error="No input file for resume.")
        return
    input_path = input_files[0]

    store.update(run_id, status=RunStatus.RUNNING, resumable=False, cancel_requested=False)
    run_settings = settings.model_copy(update={"output_dir": run.output_dir})

    def report(line: str) -> None:
        store.append_progress(run_id, line)

    try:
        outcome = service.resume(input_path, run_settings, report=report)
    except StageError as exc:
        logger.info("api.resume_failed run=%s stage=%s", run_id, exc.stage_name)
        partial = _collect_partial_artifacts(run_settings.output_dir)
        status = RunStatus.PARTIALLY_COMPLETED if partial else RunStatus.FAILED
        store.update(
            run_id,
            status=status,
            error=_safe_error(exc),
            failed_stage=exc.stage_name,
            attempt_history=list(exc.attempts),
            artifacts=partial,
            resumable=True,
            finished_at=datetime.now(UTC),
        )
        return
    except QAOpsError as exc:
        store.update(run_id, status=RunStatus.FAILED, error=_safe_error(exc))
        return
    except Exception as exc:  # noqa: BLE001
        logger.exception("api.resume_crashed run=%s", run_id)
        store.update(run_id, status=RunStatus.FAILED, error=_safe_error(exc))
        return

    store.update(
        run_id,
        status=RunStatus.COMPLETED,
        entry_point=outcome.entry_point.value,
        detection=(outcome.detection.description if outcome.detection else None),
        summary=summarize(outcome.result),
        resumable=False,
        finished_at=datetime.now(UTC),
        artifacts=[
            ArtifactMeta(name=a.name, format=a.format, path=a.path) for a in outcome.artifacts
        ],
    )


def execute_run(
    store: RunStore,
    run_id: str,
    settings: QAOpsSettings,
    service: DesignService,
) -> None:
    """Run the design workflow for a queued run, updating its state."""
    run = store.get(run_id)
    if run is None:  # pragma: no cover - scheduled only for existing runs
        return

    input_files = list(run.input_dir.iterdir())
    if not input_files:  # pragma: no cover - upload always writes one file
        store.update(
            run_id, status=RunStatus.FAILED, error="No input file was stored for this run."
        )
        return
    input_path = input_files[0]

    store.update(run_id, status=RunStatus.RUNNING, started_at=datetime.now(UTC))
    run_settings = settings.model_copy(update={"output_dir": run.output_dir})

    # Phase 28 (ADR-043): the SupervisorAgent coordinates the specialized
    # planning/execution/reflection agents. It builds an execution plan before
    # running - reasoning ABOUT execution; the deterministic pipeline still
    # performs the run below. Plan generation is best-effort; a failure here must
    # never block the actual run. Behaviour is identical to the Phase-27
    # OrchestratorAgent, which now delegates to this same supervisor.
    agent = SupervisorAgent(service)
    try:
        plan = agent.plan(input_path, run_settings)
        store.update(run_id, plan=plan.model_dump(mode="json"))
    except Exception:  # noqa: BLE001 - planning must not break execution
        logger.info("api.plan_failed run=%s", run_id)

    def report(line: str) -> None:
        store.append_progress(run_id, line)

    # Track per-stage status from execution events (ADR-040). Additive: this
    # populates run.stage_statuses for the UI without changing pipeline flow.
    def _record_stage(stage: str, status: str) -> None:
        prior = store.get(run_id)
        if prior is None:
            return
        stages = list(prior.stage_statuses)
        now = datetime.now(UTC).isoformat()
        for entry in stages:
            if entry.get("stage") == stage:
                entry["status"] = status
                if status in ("completed", "failed"):
                    entry["finished_at"] = now
                break
        else:
            stages.append({"stage": stage, "status": status, "started_at": now})
        store.update(run_id, stage_statuses=stages)

    def on_event(event: ExecutionEvent) -> None:
        # Translate structured execution events into run progress. No log-string
        # parsing, no secrets - event fields are provider/model names and
        # composed-safe messages only (ADR-029).
        # provider_call_number is a running total for the stage; stage/model
        # boundary events do not carry it, so carry the prior value forward
        # rather than letting a later event reset the visible count to 0. It
        # resets naturally when a new stage starts (ADR-030).
        prior = store.get(run_id)
        carried = 0
        if (
            prior is not None
            and prior.execution.current_stage == event.stage
            and event.provider_call_number == 0
        ):
            carried = prior.execution.provider_call_number
        progress = RunProgress(
            current_stage=event.stage,
            stage_index=event.stage_index,
            stage_count=event.stage_count,
            provider=event.provider,
            model=event.model,
            model_attempt_number=event.model_attempt_number,
            request_attempt=event.request_attempt,
            provider_call_number=event.provider_call_number or carried,
            models_attempted=event.models_attempted,
            recovery_attempts=event.recovery_attempts,
            message=event.message or render_line(event).strip(),
        )
        updates: dict[str, object] = {"execution": progress}
        if event.recovery_attempts:
            updates["recovery_attempts"] = event.recovery_attempts
        if event.type is EventType.STAGE_STARTED:
            _record_stage(event.stage, "running")
        elif event.type is EventType.STAGE_COMPLETED:
            _record_stage(event.stage, "completed")
        if event.type is EventType.STAGE_FAILED:
            updates["failed_stage"] = event.stage
            _record_stage(event.stage, "failed")
        store.update(run_id, **updates)

    try:
        _, outcome, loop_summary = agent.execute_until_goal(
            input_path, run_settings, report=report, events=on_event
        )
    except StageError as exc:
        # A stage exhausted its bounded recovery and the loop could not recover
        # (e.g. nothing to resume). Partial artifacts (from completed stages)
        # were written to the workspace by the service; expose them and mark the
        # run resumable so completed work is never lost (ADR-040). Records the
        # failed stage and sanitized attempt history so the API can show the full
        # failover story (ADR-035, section 13).
        logger.info("api.run_failed run=%s stage=%s", run_id, exc.stage_name)
        partial = _collect_partial_artifacts(run_settings.output_dir)
        status = RunStatus.PARTIALLY_COMPLETED if partial else RunStatus.FAILED
        failure_reflection: dict[str, object] | None = None
        try:
            reflection = agent.reflect(
                None, run_settings, failed_stage=exc.stage_name, attempt_history=list(exc.attempts)
            )
            failure_reflection = reflection.model_dump(mode="json")
        except Exception:  # noqa: BLE001 - advisory
            logger.info("api.reflection_failed run=%s", run_id)
        store.update(
            run_id,
            status=status,
            error=_safe_error(exc),
            failed_stage=exc.stage_name,
            attempt_history=list(exc.attempts),
            artifacts=partial,
            resumable=True,
            reflection=failure_reflection,
            finished_at=datetime.now(UTC),
        )
        return
    except QAOpsError as exc:
        # Other expected domain failures: bad input, config.
        logger.info("api.run_failed run=%s error=%s", run_id, exc.__class__.__name__)
        store.update(run_id, status=RunStatus.FAILED, error=_safe_error(exc))
        return
    except Exception as exc:  # noqa: BLE001 - unexpected, but must not crash the worker
        logger.exception("api.run_crashed run=%s", run_id)
        store.update(run_id, status=RunStatus.FAILED, error=_safe_error(exc))
        return

    # Phase 27 (ADR-042): the goal-driven loop manages execution and returns a
    # summary (iterations, decisions, terminal reason, cumulative reflection).
    # The loop's reflection is authoritative; store it and the loop summary.
    reflection_payload: dict[str, object] | None = None
    loop_payload: dict[str, object] | None = None
    try:
        loop_payload = loop_summary.model_dump(mode="json")
        reflection_payload = loop_summary.reflection.model_dump(mode="json")
    except Exception:  # noqa: BLE001 - advisory
        logger.info("api.loop_summary_failed run=%s", run_id)

    if outcome is None:
        # The loop stopped without a complete result (max resume attempts, or
        # manual review needed). Completed work is still available as partial
        # artifacts; expose them and keep the run resumable. Surface the
        # underlying stage error and attempt history, not just the terminal
        # reason, so the failure detail (ADR-035) is preserved.
        partial = _collect_partial_artifacts(run_settings.output_dir)
        status = RunStatus.PARTIALLY_COMPLETED if partial else RunStatus.FAILED
        store.update(
            run_id,
            status=status,
            error=_redact(loop_summary.last_error)
            if loop_summary.last_error
            else f"Execution stopped: {loop_summary.terminal_reason}.",
            failed_stage=loop_summary.last_failed_stage,
            attempt_history=list(loop_summary.last_attempts),
            artifacts=partial,
            resumable=True,
            reflection=reflection_payload,
            loop_summary=loop_payload,
            finished_at=datetime.now(UTC),
        )
        return

    # Phase 30 (ADR-045): deterministic quality review, COMPLETED runs only,
    # after the supervisor has finished. Advisory and read-only; never gates.
    review_payload, review_artifact = _build_review(outcome.result, run_settings.output_dir)
    run_artifacts = [
        ArtifactMeta(name=a.name, format=a.format, path=a.path) for a in outcome.artifacts
    ]
    if review_artifact is not None:
        run_artifacts.append(review_artifact)

    store.update(
        run_id,
        status=RunStatus.COMPLETED,
        entry_point=outcome.entry_point.value,
        detection=(outcome.detection.description if outcome.detection else None),
        summary=summarize(outcome.result),
        finished_at=datetime.now(UTC),
        reflection=reflection_payload,
        loop_summary=loop_payload,
        review=review_payload,
        artifacts=run_artifacts,
    )
