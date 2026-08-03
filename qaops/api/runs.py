"""In-memory run registry for the API (ADR-028).

A run is one design submission: it moves through queued -> running ->
completed | failed, and owns an isolated workspace (input/ and output/) so
concurrent runs cannot touch each other's files.

The store is deliberately behind a small interface. Phase 16 is local and
single-process, so an in-memory dict with a lock is sufficient - but a
persistent store (a database, a job queue) can replace `RunStore` without the
API layer changing, because the API depends only on these methods.

Known limitation, by design: run state lives in memory, so a process restart
loses every run and its status. Workspaces on disk survive, but the registry
that indexes them does not.
"""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    # Phase 25 (ADR-040), additive - existing four are unchanged so old clients
    # and stored responses keep working.
    PARTIALLY_COMPLETED = "partially_completed"
    RESUMABLE = "resumable"
    CANCELLED = "cancelled"


@dataclass
class ArtifactMeta:
    """One report file a run produced."""

    name: str
    format: str
    path: Path


@dataclass
class RunProgress:
    """Live execution progress for a running run (ADR-029, ADR-030).

    Populated from structured execution events, never from log strings. Every
    field is safe to expose - provider and model names are not secrets.
    """

    current_stage: str | None = None
    stage_index: int = 0
    stage_count: int = 0
    provider: str | None = None
    model: str | None = None
    model_attempt_number: int = 0
    request_attempt: int = 0
    provider_call_number: int = 0
    models_attempted: int = 0
    recovery_attempts: int = 0
    message: str = ""


@dataclass
class Run:
    """One design submission and its evolving state."""

    id: str
    status: RunStatus
    workspace: Path
    created_at: datetime
    input_name: str
    entry_point: str | None = None
    detection: str | None = None
    summary: dict[str, float | int] | None = None
    artifacts: list[ArtifactMeta] = field(default_factory=list)
    error: str | None = None
    progress: list[str] = field(default_factory=list)
    execution: RunProgress = field(default_factory=RunProgress)
    failed_stage: str | None = None
    recovery_attempts: int = 0
    # Sanitized ordered failure history (ADR-035): list of dicts with
    # stage/provider/model/failure_kind/status_code/error_code.
    attempt_history: list[dict[str, object]] = field(default_factory=list)
    # Phase 25 (ADR-040), all additive with safe defaults:
    # per-stage status records (name/status/timestamps) surfaced to the UI;
    # whether a failed/partial run can be resumed; and a cooperative cancel flag
    # checked at stage boundaries.
    stage_statuses: list[dict[str, object]] = field(default_factory=list)
    resumable: bool = False
    cancel_requested: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # Phase 26 (ADR-041), additive: the orchestrator agent's execution plan and
    # post-run reflection, stored as plain dicts (schema-agnostic) so the API can
    # surface them without the runs layer depending on the agent package.
    plan: dict[str, object] | None = None
    reflection: dict[str, object] | None = None

    @property
    def input_dir(self) -> Path:
        return self.workspace / "input"

    @property
    def output_dir(self) -> Path:
        return self.workspace / "output"


class RunStore:
    """Thread-safe in-memory registry of runs.

    Every mutating operation copies nothing and holds the lock only briefly.
    The lock matters because background execution updates a run from a worker
    thread while HTTP handlers read it.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()
        self._root.mkdir(parents=True, exist_ok=True)

    def create(self, input_name: str) -> Run:
        """Register a queued run with an isolated workspace."""
        run_id = f"run_{uuid.uuid4().hex[:16]}"
        workspace = self._root / run_id
        run = Run(
            id=run_id,
            status=RunStatus.QUEUED,
            workspace=workspace,
            created_at=datetime.now(UTC),
            input_name=input_name,
        )
        run.input_dir.mkdir(parents=True, exist_ok=True)
        run.output_dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._runs[run_id] = run
        return run

    def get(self, run_id: str) -> Run | None:
        with self._lock:
            return self._runs.get(run_id)

    def update(self, run_id: str, **fields: object) -> None:
        """Set attributes on a run atomically."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            for key, value in fields.items():
                setattr(run, key, value)

    def append_progress(self, run_id: str, line: str) -> None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is not None:
                run.progress.append(line)

    def all(self) -> list[Run]:
        with self._lock:
            return list(self._runs.values())
