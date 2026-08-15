"""Clarification state persistence (Phase 41A).

Mirrors the Phase 36B evidence sidecar pattern: a single deterministic JSON file in
the run workspace, separate from input/ and output/. Missing file -> None (a run
with no clarification yet); corrupt/malformed -> ClarificationStateError (fail
clearly, never silently discard a user's answers). No LLM or pipeline logic.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from qaops.clarification.models import ClarificationState

if TYPE_CHECKING:
    from pathlib import Path

_CLARIFICATION_DIRNAME = "clarification"
_STATE_FILENAME = "state.json"


class ClarificationStateError(RuntimeError):
    """The clarification state file exists but could not be read or reconstructed.

    Raised on corrupt/unreadable content so a resumed clarification fails clearly
    rather than silently losing prior questions and answers.
    """


def clarification_state_path(workspace: Path) -> Path:
    """Path to the clarification state file for a run workspace."""
    return workspace / _CLARIFICATION_DIRNAME / _STATE_FILENAME


def write_clarification_state(workspace: Path, state: ClarificationState) -> Path:
    """Serialize clarification state to the run workspace (deterministic JSON)."""
    target = clarification_state_path(workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = state.model_dump(mode="json")
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return target


def load_clarification_state(workspace: Path) -> ClarificationState | None:
    """Reconstruct clarification state from the workspace, or None if absent.

    Returns None when no state file exists (clarification not started), so callers
    can treat that as "no clarification in progress". Raises ClarificationStateError
    if the file exists but is corrupt or fails model validation.
    """
    path = clarification_state_path(workspace)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ClarificationStateError(f"Clarification state is unreadable: {exc}") from exc
    try:
        return ClarificationState.model_validate(raw)
    except Exception as exc:  # noqa: BLE001 - reconstruction failure must fail clearly
        raise ClarificationStateError(
            f"Clarification state could not be reconstructed: {exc}"
        ) from exc
