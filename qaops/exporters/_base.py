"""Shared exporter helpers.

The canonical serialization lives here: `to_canonical_dict` runs the
result through Pydantic's own JSON-mode dump, which fixes field names,
declaration-order ordering, and enum values once. Every exporter derives
its output from this one dict, so CSV columns, Markdown sections, and
Excel sheets can never drift from the JSON shape (ADR-016).

Determinism rules enforced here: no timestamps, no random IDs, no set
iteration leaking into output. List fields are joined with a stable
separator; nothing is sorted into a different order than the models
declare, because declaration order is already stable and meaningful.
"""

from typing import Any

from qaops.models import TestDesignResult

LIST_SEPARATOR = "; "


def to_canonical_dict(result: TestDesignResult) -> dict[str, Any]:
    """The single source of truth for serialized shape (ADR-016).

    JSON mode renders enums as their values and produces only JSON-native
    types. The input is never mutated - model_dump reads, never writes.
    """
    return result.model_dump(mode="json")


def join_list(values: list[str]) -> str:
    """Join a list of strings with the stable separator for flat formats."""
    return LIST_SEPARATOR.join(values)


def join_test_data(test_data: dict[str, str]) -> str:
    """Render a test_data mapping deterministically as 'k=v; k=v'.

    Keys are emitted in insertion order (Python dicts preserve it, and the
    upstream wire schema built them in a fixed order), so output is stable
    without imposing alphabetical reordering.
    """
    return LIST_SEPARATOR.join(f"{k}={v}" for k, v in test_data.items())


def format_steps(steps: list[dict[str, Any]]) -> str:
    """Render ordered steps as 'N. action -> expected' lines for flat formats."""
    lines: list[str] = []
    for step in steps:
        number = step["number"]
        action = step["action"]
        expected = step.get("expected", "")
        line = f"{number}. {action}"
        if expected:
            line += f" -> {expected}"
        lines.append(line)
    return "\n".join(lines)
