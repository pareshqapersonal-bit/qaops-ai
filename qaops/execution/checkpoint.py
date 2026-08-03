"""Per-stage execution checkpoints (ADR-040, Phase 25).

A checkpoint is the JSON-serialized output of one successfully completed pipeline
stage, written to the run workspace so that:

  * partial artifacts can be exported from the furthest completed stage even when
    a later stage fails (never discard completed work), and
  * a failed run can resume from the last successful checkpoint without
    re-running completed stages.

Design facts this relies on (verified against the domain models):

  * Stage outputs are CUMULATIVE - each stage returns a Pydantic model that
    nests every prior output (ScenarioDesignResult.analysis holds the requirement
    analysis; ConditionDesignResult.scenario_design holds that; TestDesignResult
    holds everything). So the latest checkpoint is a complete snapshot of
    progress-so-far, and it doubles as the exact resume input for the next stage.
  * Every stage output is a Pydantic BaseModel, so it round-trips through
    model_dump(mode="json") / model_validate deterministically.

This module is pure disk I/O and (de)serialization - no LLM, no provider calls,
no pipeline logic. It never changes stage behaviour; the executor calls it only
at stage boundaries via an optional callback, so a run with no checkpoint sink
(CLI, most tests) behaves exactly as before.

Scope (Phase 25): in-process resume with disk checkpoints. Reconstructing the
in-memory run registry after a server restart is explicitly out of scope and
left as a future enhancement (ADR-040).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

# Maps each pipeline result type name to its class, so a checkpoint can be
# rehydrated into the exact model the next stage expects. Imported at runtime
# because the dict holds the concrete classes; only the annotation types below
# are deferred.
from qaops.models import (
    ConditionDesignResult,
    RequirementAnalysisResult,
    ScenarioDesignResult,
    TestDesignResult,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel

_RESULT_TYPES: dict[str, type[BaseModel]] = {
    RequirementAnalysisResult.__name__: RequirementAnalysisResult,
    ScenarioDesignResult.__name__: ScenarioDesignResult,
    ConditionDesignResult.__name__: ConditionDesignResult,
    TestDesignResult.__name__: TestDesignResult,
}

CHECKPOINT_DIRNAME = "checkpoints"
MANIFEST_NAME = "manifest.json"

# Placeholder re-injected for the required source_text field when a checkpoint is
# rehydrated. The real text is excluded from checkpoints (see write_stage); it is
# never read downstream, so a marker keeps the model valid without carrying the
# document. Resume feeds a stage's *analysis*, not raw text, so this is inert.
_SOURCE_TEXT_PLACEHOLDER = "[source_text omitted from checkpoint]"


def _as_int(value: object) -> int:
    """Coerce a manifest value to int for sorting; unknown -> 0."""
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _strip_source_text(value: object) -> object:
    """Recursively remove every 'source_text' key from a dumped model.

    Model-shape-agnostic: source_text can appear at the top level
    (RequirementAnalysisResult) or nested (ScenarioDesignResult.analysis,
    ConditionDesignResult.scenario_design.analysis), so we walk the whole tree.
    """
    if isinstance(value, dict):
        return {k: _strip_source_text(v) for k, v in value.items() if k != "source_text"}
    if isinstance(value, list):
        return [_strip_source_text(v) for v in value]
    return value


def _reinject_source_text(value: object) -> object:
    """Restore a placeholder source_text wherever an analysis object needs it.

    An analysis dict is recognised by carrying 'requirements' and 'gap_report'
    (its required siblings) without 'source_text'. We re-add the placeholder so
    model_validate succeeds; the value is never used downstream.
    """
    if isinstance(value, dict):
        restored = {k: _reinject_source_text(v) for k, v in value.items()}
        if (
            "requirements" in restored
            and "gap_report" in restored
            and "source_name" in restored
            and "source_text" not in restored
        ):
            restored["source_text"] = _SOURCE_TEXT_PLACEHOLDER
        return restored
    if isinstance(value, list):
        return [_reinject_source_text(v) for v in value]
    return value


class StageStatus(StrEnum):
    """Per-stage execution status (ADR-040)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True)
class StageCheckpoint:
    """One persisted stage output plus its execution metadata."""

    stage_name: str
    stage_index: int
    status: StageStatus
    result_type: str
    result: BaseModel
    started_at: str | None = None
    completed_at: str | None = None


class CheckpointError(Exception):
    """A checkpoint could not be read or rehydrated (corrupt or unknown type)."""


class CheckpointStore:
    """Reads and writes per-stage checkpoints under a run's workspace.

    Layout, under `<output_dir>/checkpoints/`:
        NN_<stage_name>.json   one per completed stage (NN = zero-padded index)
        manifest.json          ordered stage statuses + timestamps
    """

    def __init__(self, output_dir: Path) -> None:
        self._dir = output_dir / CHECKPOINT_DIRNAME

    @property
    def directory(self) -> Path:
        return self._dir

    def _checkpoint_path(self, stage_index: int, stage_name: str) -> Path:
        return self._dir / f"{stage_index:02d}_{stage_name}.json"

    @property
    def _manifest_path(self) -> Path:
        return self._dir / MANIFEST_NAME

    # --- writing -------------------------------------------------------------

    def write_stage(
        self,
        stage_name: str,
        stage_index: int,
        result: BaseModel,
        *,
        started_at: str | None = None,
    ) -> None:
        """Persist one completed stage's output and update the manifest.

        Atomic per file (write to a temp sibling, then replace) so a crash mid
        write cannot leave a half-written checkpoint that later fails to parse.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        completed_at = datetime.now(UTC).isoformat()
        # Exclude the raw source_text from every checkpoint (ADR-040). It is the
        # unmodified input, already persisted in the run's input/ dir, and no
        # downstream stage or exporter reads it - only the first stage produces
        # it. Because stage outputs are cumulative and nested, keeping it would
        # duplicate the whole document in every checkpoint (measured ~7x on a
        # typical run). Stripping it removes the dominant redundancy with no
        # effect on resume or partial export.
        dumped = _strip_source_text(result.model_dump(mode="json"))
        payload = {
            "stage_name": stage_name,
            "stage_index": stage_index,
            "status": StageStatus.COMPLETED.value,
            "result_type": type(result).__name__,
            "started_at": started_at,
            "completed_at": completed_at,
            "result": dumped,
        }
        self._atomic_write(self._checkpoint_path(stage_index, stage_name), payload)
        self._record_in_manifest(
            stage_name, stage_index, StageStatus.COMPLETED, started_at, completed_at
        )

    def mark_stage(
        self,
        stage_name: str,
        stage_index: int,
        status: StageStatus,
        *,
        started_at: str | None = None,
    ) -> None:
        """Record a stage's status in the manifest without a result payload.

        Used for running/failed/skipped transitions; only COMPLETED stages carry
        a checkpoint file (there is nothing to persist for a stage that has not
        produced output).
        """
        completed_at = datetime.now(UTC).isoformat() if status is StageStatus.FAILED else None
        self._record_in_manifest(stage_name, stage_index, status, started_at, completed_at)

    # --- reading -------------------------------------------------------------

    def manifest(self) -> list[dict[str, object]]:
        """Ordered stage-status records, or [] if no manifest exists yet."""
        if not self._manifest_path.exists():
            return []
        try:
            data = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckpointError(f"Could not read checkpoint manifest: {exc}") from exc
        stages = data.get("stages", []) if isinstance(data, dict) else []
        return [s for s in stages if isinstance(s, dict)]

    def completed_stages(self) -> list[str]:
        """Names of stages recorded COMPLETED, in order."""
        return [
            str(s["stage_name"])
            for s in self.manifest()
            if s.get("status") == StageStatus.COMPLETED.value and "stage_name" in s
        ]

    def latest_checkpoint(self) -> StageCheckpoint | None:
        """The highest-index COMPLETED checkpoint, rehydrated, or None.

        This is both the partial-export source and the resume input.
        """
        best: tuple[int, Path] | None = None
        if not self._dir.exists():
            return None
        for path in self._dir.glob("*.json"):
            if path.name == MANIFEST_NAME:
                continue
            index = self._index_from_name(path.name)
            if index is None:
                continue
            if best is None or index > best[0]:
                best = (index, path)
        if best is None:
            return None
        return self._load_checkpoint(best[1])

    def load_stage(self, stage_index: int, stage_name: str) -> StageCheckpoint | None:
        path = self._checkpoint_path(stage_index, stage_name)
        if not path.exists():
            return None
        return self._load_checkpoint(path)

    # --- internals -----------------------------------------------------------

    def _load_checkpoint(self, path: Path) -> StageCheckpoint:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckpointError(f"Corrupt checkpoint {path.name}: {exc}") from exc
        if not isinstance(data, dict):
            raise CheckpointError(f"Corrupt checkpoint {path.name}: not an object.")
        result_type = data.get("result_type")
        model_cls = _RESULT_TYPES.get(str(result_type))
        if model_cls is None:
            raise CheckpointError(
                f"Checkpoint {path.name} has unknown result_type {result_type!r}."
            )
        try:
            result = model_cls.model_validate(_reinject_source_text(data["result"]))
        except Exception as exc:  # noqa: BLE001 - pydantic ValidationError + KeyError
            raise CheckpointError(
                f"Checkpoint {path.name} could not be rehydrated into {result_type}: {exc}"
            ) from exc
        status_raw = str(data.get("status", StageStatus.COMPLETED.value))
        try:
            status = StageStatus(status_raw)
        except ValueError:
            status = StageStatus.COMPLETED
        return StageCheckpoint(
            stage_name=str(data.get("stage_name", "")),
            stage_index=int(data.get("stage_index", 0)),
            status=status,
            result_type=str(result_type),
            result=result,
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
        )

    def _record_in_manifest(
        self,
        stage_name: str,
        stage_index: int,
        status: StageStatus,
        started_at: str | None,
        completed_at: str | None,
    ) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        stages: list[dict[str, object]] = []
        if self._manifest_path.exists():
            try:
                existing = json.loads(self._manifest_path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    raw = existing.get("stages", [])
                    stages = [s for s in raw if isinstance(s, dict)]
            except (OSError, json.JSONDecodeError):
                stages = []
        # Replace any prior record for this stage index, else append.
        record: dict[str, object] = {
            "stage_name": stage_name,
            "stage_index": stage_index,
            "status": status.value,
            "started_at": started_at,
            "completed_at": completed_at,
        }
        replaced = False
        for i, s in enumerate(stages):
            if s.get("stage_index") == stage_index:
                stages[i] = record
                replaced = True
                break
        if not replaced:
            stages.append(record)
        stages.sort(key=lambda s: _as_int(s.get("stage_index", 0)))
        self._atomic_write(self._manifest_path, {"stages": stages})

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, object]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def _index_from_name(name: str) -> int | None:
        head = name.split("_", 1)[0]
        return int(head) if head.isdigit() else None
