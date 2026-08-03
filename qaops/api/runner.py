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

from qaops.agent import OrchestratorAgent
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


def _safe_error(exc: Exception) -> str:
    """A client-safe message: the exception text with any secrets redacted."""
    message = str(exc) or exc.__class__.__name__
    for pattern in _SECRET_PATTERNS:
        message = pattern.sub("[redacted]", message)
    return message


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

    # Phase 26 (ADR-041): the orchestrator agent builds an execution plan before
    # running. This is reasoning ABOUT execution - the deterministic pipeline
    # still performs the run below. Plan generation is best-effort; a failure
    # here must never block the actual run.
    agent = OrchestratorAgent(service)
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
        outcome = service.run(input_path, run_settings, report=report, events=on_event)
    except StageError as exc:
        # A stage exhausted its bounded recovery. Partial artifacts (from
        # completed stages) were written to the workspace by the service; expose
        # them and mark the run resumable so completed work is never lost
        # (ADR-040). Records the failed stage and sanitized attempt history so
        # the API can show the full failover story (ADR-035, section 13).
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

    # Phase 26 (ADR-041): produce a post-run reflection (reasoning only; never
    # regenerates artifacts). Best-effort - a reflection failure must not turn a
    # successful run into a failed one.
    reflection_payload: dict[str, object] | None = None
    try:
        reflection = agent.reflect(outcome, run_settings)
        reflection_payload = reflection.model_dump(mode="json")
    except Exception:  # noqa: BLE001 - reflection is advisory
        logger.info("api.reflection_failed run=%s", run_id)

    store.update(
        run_id,
        status=RunStatus.COMPLETED,
        entry_point=outcome.entry_point.value,
        detection=(outcome.detection.description if outcome.detection else None),
        summary=summarize(outcome.result),
        finished_at=datetime.now(UTC),
        reflection=reflection_payload,
        artifacts=[
            ArtifactMeta(name=a.name, format=a.format, path=a.path) for a in outcome.artifacts
        ],
    )
