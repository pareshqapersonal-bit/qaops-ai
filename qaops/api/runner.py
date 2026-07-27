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

    store.update(run_id, status=RunStatus.RUNNING)
    run_settings = settings.model_copy(update={"output_dir": run.output_dir})

    def report(line: str) -> None:
        store.append_progress(run_id, line)

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
        if event.type is EventType.STAGE_FAILED:
            updates["failed_stage"] = event.stage
        store.update(run_id, **updates)

    try:
        outcome = service.run(input_path, run_settings, report=report, events=on_event)
    except StageError as exc:
        # A stage exhausted its bounded recovery. Record which stage, for a
        # useful failure representation (section 13).
        logger.info("api.run_failed run=%s stage=%s", run_id, exc.stage_name)
        store.update(
            run_id,
            status=RunStatus.FAILED,
            error=_safe_error(exc),
            failed_stage=exc.stage_name,
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

    store.update(
        run_id,
        status=RunStatus.COMPLETED,
        entry_point=outcome.entry_point.value,
        detection=(outcome.detection.description if outcome.detection else None),
        summary=summarize(outcome.result),
        artifacts=[
            ArtifactMeta(name=a.name, format=a.format, path=a.path) for a in outcome.artifacts
        ],
    )
