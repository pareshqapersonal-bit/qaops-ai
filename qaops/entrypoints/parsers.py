"""Input parsers - external files into canonical domain models (ADR-022).

The mirror image of exporters: exporters turn domain models into files,
parsers turn files into domain models. They contain NO generation logic and
make NO LLM calls - they only transform and validate structure, so a
malformed input fails here with a clear message rather than deep inside a
pipeline stage.

Supported formats: JSON (the canonical export shape round-trips) and CSV
(matching the csv-bundle column layout, so an exported bundle can be edited
and fed back in).

IDs are reassigned deterministically by code, never trusted from the file,
consistent with ADR-001: only code assigns IDs.
"""

import csv
import io
import json
from pathlib import Path
from typing import Any

from qaops.core.errors import DocumentLoadError
from qaops.core.ids import requirement_ids, scenario_ids
from qaops.exporters._base import LIST_SEPARATOR
from qaops.models import (
    Requirement,
    RequirementAnalysisResult,
    Scenario,
    ScenarioCategory,
    ScenarioDesignResult,
)


def _split_list(value: str) -> list[str]:
    """Parse a joined list field back into its parts."""
    if not value or not value.strip():
        return []
    separator = LIST_SEPARATOR.strip()
    parts = [p.strip() for p in value.split(separator)]
    return [p for p in parts if p]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"Could not read {path}: {exc}"
        raise DocumentLoadError(msg) from exc


def _load_json(path: Path) -> Any:
    try:
        return json.loads(_read_text(path))
    except json.JSONDecodeError as exc:
        msg = f"{path} is not valid JSON: {exc}"
        raise DocumentLoadError(msg) from exc


def _rows(path: Path) -> list[dict[str, str]]:
    text = _read_text(path)
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        msg = f"{path} contains no data rows."
        raise DocumentLoadError(msg)
    return rows


def _require_columns(path: Path, row: dict[str, str], required: set[str]) -> None:
    missing = sorted(required - set(row))
    if missing:
        msg = f"{path} is missing required column(s): {missing}. Found columns: {sorted(row)}."
        raise DocumentLoadError(msg)


# --- requirements ------------------------------------------------------------


def parse_requirements(path: Path) -> RequirementAnalysisResult:
    """Load requirements from JSON or CSV into a RequirementAnalysisResult.

    Accepted JSON shapes: a bare list of requirement objects, or an object
    with a "requirements" key (so a QAOps JSON export can be fed straight
    back in).
    """
    suffix = path.suffix.lower()
    if suffix == ".json":
        raw = _load_json(path)
        items = raw.get("requirements") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            msg = f"{path} must contain a list of requirements or a 'requirements' key."
            raise DocumentLoadError(msg)
        records = [r for r in items if isinstance(r, dict)]
    elif suffix == ".csv":
        rows = _rows(path)
        _require_columns(path, rows[0], {"title"})
        records = [
            {
                "title": row.get("title", ""),
                "description": row.get("description", ""),
                "source_excerpt": row.get("source_excerpt", ""),
                "actors": _split_list(row.get("actors", "")),
                "inputs": _split_list(row.get("inputs", "")),
                "outputs": _split_list(row.get("outputs", "")),
                "validations": _split_list(row.get("validations", "")),
                "dependencies": _split_list(row.get("dependencies", "")),
                "constraints": _split_list(row.get("constraints", "")),
                "assumptions": _split_list(row.get("assumptions", "")),
            }
            for row in rows
        ]
    else:
        msg = f"Requirements input must be .json or .csv, got {suffix!r}."
        raise DocumentLoadError(msg)

    if not records:
        msg = f"No requirements found in {path}."
        raise DocumentLoadError(msg)

    ids = requirement_ids()
    requirements: list[Requirement] = []
    for record in records:
        payload = dict(record)
        payload.pop("id", None)  # IDs are assigned by code, never trusted
        if not str(payload.get("title", "")).strip():
            msg = f"A requirement in {path} has an empty title."
            raise DocumentLoadError(msg)
        payload.setdefault("description", payload["title"])
        try:
            requirements.append(Requirement(id=ids.next(), **payload))
        except (TypeError, ValueError) as exc:
            msg = f"Invalid requirement in {path}: {exc}"
            raise DocumentLoadError(msg) from exc

    return RequirementAnalysisResult(
        source_name=path.name,
        source_text=f"Requirements imported from {path.name}",
        requirements=requirements,
    )


# --- scenarios ---------------------------------------------------------------


def parse_scenarios(path: Path) -> ScenarioDesignResult:
    """Load scenarios from JSON or CSV into a ScenarioDesignResult.

    A ScenarioDesignResult carries the requirement analysis its scenarios
    trace to, because TestCaseGenerator validates requirement references
    (ADR-014). If the input supplies requirements they are used; otherwise
    minimal placeholder requirements are synthesized from the requirement
    IDs the scenarios reference, so a bare scenario file still works.
    """
    suffix = path.suffix.lower()
    supplied_requirements: list[dict[str, Any]] = []

    if suffix == ".json":
        raw = _load_json(path)
        if isinstance(raw, dict):
            items = raw.get("scenarios")
            supplied = raw.get("requirements", [])
            if isinstance(supplied, list):
                supplied_requirements = [r for r in supplied if isinstance(r, dict)]
        else:
            items = raw
        if not isinstance(items, list):
            msg = f"{path} must contain a list of scenarios or a 'scenarios' key."
            raise DocumentLoadError(msg)
        records = [s for s in items if isinstance(s, dict)]
    elif suffix == ".csv":
        rows = _rows(path)
        _require_columns(path, rows[0], {"title"})
        records = [
            {
                "title": row.get("title", ""),
                "description": row.get("description", ""),
                "category": row.get("category", "") or "functional",
                "requirement_ids": _split_list(row.get("requirement_ids", "")),
            }
            for row in rows
        ]
    else:
        msg = f"Scenarios input must be .json or .csv, got {suffix!r}."
        raise DocumentLoadError(msg)

    if not records:
        msg = f"No scenarios found in {path}."
        raise DocumentLoadError(msg)

    # Requirements first: scenarios reference them by the ID used in the file,
    # so build a mapping from the file's IDs to freshly assigned canonical ones.
    original_to_canonical: dict[str, str] = {}
    req_ids = requirement_ids()
    requirements: list[Requirement] = []

    for record in supplied_requirements:
        payload = dict(record)
        original = str(payload.pop("id", "")).strip()
        canonical = req_ids.next()
        if original:
            original_to_canonical[original] = canonical
        payload.setdefault("description", payload.get("title", "Imported requirement"))
        try:
            requirements.append(Requirement(id=canonical, **payload))
        except (TypeError, ValueError) as exc:
            msg = f"Invalid requirement in {path}: {exc}"
            raise DocumentLoadError(msg) from exc

    # Synthesize placeholders for any requirement ID a scenario references
    # that the file did not define.
    referenced: list[str] = []
    for record in records:
        for rid in record.get("requirement_ids", []) or []:
            rid = str(rid).strip()
            if rid and rid not in original_to_canonical and rid not in referenced:
                referenced.append(rid)
    for original in referenced:
        canonical = req_ids.next()
        original_to_canonical[original] = canonical
        requirements.append(
            Requirement(
                id=canonical,
                title=f"Imported requirement {original}",
                description=(
                    f"Placeholder for requirement {original}, referenced by an "
                    f"imported scenario but not defined in {path.name}."
                ),
            )
        )

    if not requirements:
        # Scenarios with no requirement references at all: attach one
        # placeholder so downstream reference validation has something valid.
        canonical = req_ids.next()
        original_to_canonical["__unscoped__"] = canonical
        requirements.append(
            Requirement(
                id=canonical,
                title="Imported scenarios",
                description=f"Placeholder requirement for scenarios imported from {path.name}.",
            )
        )

    sc_ids = scenario_ids()
    scenarios: list[Scenario] = []
    for record in records:
        payload = dict(record)
        payload.pop("id", None)
        title = str(payload.get("title", "")).strip()
        if not title:
            msg = f"A scenario in {path} has an empty title."
            raise DocumentLoadError(msg)
        raw_category = str(payload.get("category", "") or "functional").strip().casefold()
        try:
            category = ScenarioCategory(raw_category)
        except ValueError as exc:
            valid = sorted(c.value for c in ScenarioCategory)
            msg = f"Unknown scenario category {raw_category!r} in {path}. Valid: {valid}."
            raise DocumentLoadError(msg) from exc

        mapped = [
            original_to_canonical[str(rid).strip()]
            for rid in payload.get("requirement_ids", []) or []
            if str(rid).strip() in original_to_canonical
        ]
        if not mapped:
            mapped = [requirements[0].id]

        scenarios.append(
            Scenario(
                id=sc_ids.next(),
                title=title,
                description=str(payload.get("description", "") or ""),
                category=category,
                requirement_ids=mapped,
            )
        )

    analysis = RequirementAnalysisResult(
        source_name=path.name,
        source_text=f"Scenarios imported from {path.name}",
        requirements=requirements,
    )
    return ScenarioDesignResult(analysis=analysis, scenarios=scenarios)
