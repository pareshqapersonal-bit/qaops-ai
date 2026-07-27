"""Structured execution events (ADR-029).

The executor previously reported progress as preformatted text through a single
`reporter(str)` callback. The CLI could render that, but the API had no way to
obtain structured progress without parsing those strings - which the phase
forbids.

This module defines a small event model the executor emits. The CLI renders
events as the same text as before; the API converts them into run progress.
The executor depends only on this callback type, never on HTTP, so the
dependency arrow stays one-way: executor -> events -> {CLI, API}.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class EventType(StrEnum):
    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    REQUEST_STARTED = "request_started"
    REQUEST_COMPLETED = "request_completed"
    REQUEST_FAILED = "request_failed"
    REQUEST_TIMED_OUT = "request_timed_out"
    REQUEST_RETRY = "request_retry"
    MODEL_ATTEMPT = "model_attempt"
    MODEL_FAILED = "model_failed"
    MODEL_SWITCH = "model_switch"
    PROVIDER_SWITCH = "provider_switch"
    PROVIDER_EXHAUSTED = "provider_exhausted"
    STAGE_FAILED = "stage_failed"


@dataclass(frozen=True)
class ExecutionEvent:
    """One thing that happened during execution.

    Every field is safe to expose: provider and model names are not secrets,
    and `message` is composed here from known-safe text, never from a raw
    provider payload.

    Counter semantics (ADR-030), chosen to be unambiguous:
    - model_attempt_number: which distinct model this is for the stage, 1-based.
      Model 1 is the first tried; model 2 is after one model switch, etc.
    - request_attempt: which network request for the CURRENT model, 1-based.
      Resets to 1 on every model switch. A same-model timeout retry increments
      this without changing model_attempt_number.
    - recovery_attempts: recovery actions (model/provider switches) spent on the
      stage so far. Matches the stage recovery budget.
    - models_attempted: retained for backward compatibility; equals
      model_attempt_number for a MODEL_SWITCH event. Prefer the fields above.
    """

    type: EventType
    stage: str
    stage_index: int
    stage_count: int
    provider: str | None = None
    model: str | None = None
    model_attempt_number: int = 0
    request_attempt: int = 0
    # Running total of ACTUAL provider generation calls for the stage, including
    # structured-output repair calls (ADR-030). This is the number the progress
    # model should trust when asking "how many real calls have happened".
    provider_call_number: int = 0
    models_attempted: int = 0
    recovery_attempts: int = 0
    failure_kind: str | None = None
    message: str = ""


# The executor emits events through this. Defaults to discarding.
EventSink = Callable[[ExecutionEvent], None]


def render_line(event: ExecutionEvent) -> str:
    """Render an event as a CLI progress line, matching the prior wording."""
    target = ""
    if event.provider and event.model:
        target = f"{event.provider}/{event.model}"
    elif event.provider:
        target = event.provider

    if event.type is EventType.STAGE_COMPLETED:
        return f"  {event.stage}: {target} ok"
    if event.type is EventType.REQUEST_STARTED:
        return f"  {event.stage}: calling {target} (call #{event.provider_call_number})"
    if event.type is EventType.REQUEST_COMPLETED:
        return f"  {event.stage}: {target} responded (call #{event.provider_call_number})"
    if event.type is EventType.REQUEST_FAILED:
        return f"  {event.stage}: {target} call failed ({event.failure_kind})"
    if event.type is EventType.REQUEST_TIMED_OUT:
        return f"  {event.stage}: {target} timed out"
    if event.type is EventType.REQUEST_RETRY:
        return f"  {event.stage}: retrying {target} (attempt {event.request_attempt})"
    if event.type is EventType.MODEL_FAILED:
        return f"  {event.stage}: {target} failed ({event.failure_kind})"
    if event.type is EventType.MODEL_SWITCH:
        return f"  trying {target} ({event.message})"
    if event.type is EventType.PROVIDER_SWITCH:
        return f"  switching to {target}"
    if event.type is EventType.PROVIDER_EXHAUSTED:
        return f"  {event.provider} exhausted; {event.message}"
    if event.type is EventType.STAGE_FAILED:
        return f"  {event.stage} failed: {event.message}"
    if event.type is EventType.STAGE_STARTED:
        return f"  {event.stage}: starting on {target}"
    return f"  {event.stage}: {event.message}"
