"""Deterministic input classification (ADR-025).

Decides which entry point an input belongs to, so the user does not have to
pass --from. Classification is deterministic and makes no LLM call: extensions
resolve most cases outright, and the genuinely ambiguous ones are settled by
inspecting structure - column headers, JSON keys, the presence of a scenario
table or list.

    .pdf .docx .html          -> document (prose; the analyzer handles it)
    .xlsx .xlsm               -> scenarios (a spreadsheet is a table)
    .csv .json                -> inspect: requirements or scenarios
    .md .markdown .txt        -> inspect: structured scenarios, else document

Where inspection is inconclusive the classifier prefers the document route,
because the requirement analyzer can read anything textual, whereas the
structured parsers reject what they do not recognise. Guessing "scenarios"
wrongly fails the run; guessing "document" wrongly costs extra LLM calls but
still produces output.
"""

import csv
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

from qaops.entrypoints.entry_point import EntryPoint
from qaops.entrypoints.structured_readers import (
    _ID_PATTERN,
    _KNOWN_CATEGORIES,
    _LIST_ITEM,
    _normalize_key,
)

# Extensions that admit exactly one interpretation.
_DOCUMENT_ONLY = {".pdf", ".docx", ".html", ".htm"}
_SCENARIO_ONLY = {".xlsx", ".xlsm"}
_TEXTUAL = {".md", ".markdown", ".txt"}
_TABULAR = {".csv"}
_STRUCTURED = {".json"}

# Header names that identify a table as scenarios rather than requirements.
_SCENARIO_HEADERS = {"category", "requirement_ids"}
# Header names unique to requirements exports.
_REQUIREMENT_HEADERS = {"actors", "validations", "dependencies", "constraints", "assumptions"}

_MAX_INSPECT_BYTES = 64_000


@dataclass(frozen=True)
class Classification:
    """The chosen entry point and why, for user-facing feedback."""

    entry_point: EntryPoint
    description: str
    reason: str


def _peek(path: Path) -> str:
    """Read a bounded prefix of a text file; empty string if unreadable."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(_MAX_INSPECT_BYTES)
    except OSError:
        return ""


def _classify_csv(text: str) -> tuple[EntryPoint, str, str]:
    try:
        reader = csv.reader(io.StringIO(text))
        header = next(reader, [])
    except csv.Error:
        header = []
    keys = {_normalize_key(cell) for cell in header}
    if keys & _SCENARIO_HEADERS:
        return (
            EntryPoint.SCENARIOS,
            "scenario table",
            f"CSV header contains {sorted(keys & _SCENARIO_HEADERS)}",
        )
    if keys & _REQUIREMENT_HEADERS:
        return (
            EntryPoint.REQUIREMENTS,
            "requirements table",
            f"CSV header contains {sorted(keys & _REQUIREMENT_HEADERS)}",
        )
    # A bare title/description table is ambiguous; requirements is the safer
    # reading, since scenarios without categories still parse as requirements
    # and the pipeline then generates scenarios from them.
    return (
        EntryPoint.REQUIREMENTS,
        "requirements table",
        "CSV header has no scenario-specific columns",
    )


def _classify_json(text: str) -> tuple[EntryPoint, str, str]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return (
            EntryPoint.REQUIREMENTS,
            "requirements file",
            "JSON could not be inspected; defaulting to requirements",
        )
    if isinstance(payload, dict):
        if "scenarios" in payload:
            return (EntryPoint.SCENARIOS, "scenario file", "JSON has a 'scenarios' key")
        if "requirements" in payload:
            return (
                EntryPoint.REQUIREMENTS,
                "requirements file",
                "JSON has a 'requirements' key",
            )
        items = []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    first = next((item for item in items if isinstance(item, dict)), None)
    if first is not None:
        keys = {_normalize_key(k) for k in first}
        if keys & _SCENARIO_HEADERS:
            return (
                EntryPoint.SCENARIOS,
                "scenario file",
                f"JSON records contain {sorted(keys & _SCENARIO_HEADERS)}",
            )
    return (EntryPoint.REQUIREMENTS, "requirements file", "JSON records look like requirements")


def _classify_textual(path: Path, text: str) -> tuple[EntryPoint, str, str]:
    """Structured scenarios if the document is clearly a scenario list.

    A markdown table with a title column is a reliable signal. A bare list is
    NOT: requirement documents routinely use numbered acceptance criteria and
    bulleted notes, and misreading those as scenarios sends a PRD down the
    wrong route where it fails. List items therefore only count when they carry
    an explicit scenario marker - a REQ-001 style reference or a known category
    tag - which a prose document's criteria do not.
    """
    from qaops.entrypoints.structured_readers import _read_markdown_table

    lines = text.splitlines()
    if _read_markdown_table(lines):
        return (
            EntryPoint.SCENARIOS,
            "scenario table",
            "document contains a scenario table",
        )

    marked = 0
    total = 0
    for line in lines:
        match = _LIST_ITEM.match(line)
        if not match:
            continue
        total += 1
        item = match.group(1)
        if _ID_PATTERN.search(item):
            marked += 1
            continue
        parenthetical = re.search(r"\(([^()]+)\)", item)
        if parenthetical:
            candidate = parenthetical.group(1).strip().casefold().replace(" ", "_")
            if candidate in _KNOWN_CATEGORIES:
                marked += 1

    # Require most list items to be explicitly marked, so a document with one
    # stray "REQ-001" mention in its notes is not mistaken for a scenario list.
    if total and marked >= max(2, (total + 1) // 2):
        return (
            EntryPoint.SCENARIOS,
            "scenario list",
            f"{marked} of {total} list items carry scenario markers",
        )
    return (
        EntryPoint.DOCUMENT,
        "requirement document",
        "no scenario table or marked scenario list found; treating as prose",
    )


def classify_input(path: Path) -> Classification:
    """Choose an entry point for `path` by extension, then by content."""
    suffix = path.suffix.lower()

    if suffix in _DOCUMENT_ONLY:
        return Classification(
            EntryPoint.DOCUMENT,
            "requirement document",
            f"{suffix} is a prose document format",
        )
    if suffix in _SCENARIO_ONLY:
        return Classification(
            EntryPoint.SCENARIOS,
            "scenario spreadsheet",
            f"{suffix} is a spreadsheet of scenarios",
        )

    text = _peek(path)
    if suffix in _TABULAR:
        entry, description, reason = _classify_csv(text)
    elif suffix in _STRUCTURED:
        entry, description, reason = _classify_json(text)
    elif suffix in _TEXTUAL:
        entry, description, reason = _classify_textual(path, text)
    else:
        # Unknown extension: let the document route's ingestion layer produce
        # its own precise "unsupported format" error rather than guessing here.
        return Classification(
            EntryPoint.DOCUMENT,
            "unrecognised input",
            f"{suffix or 'no extension'} is not a known QAOps input format",
        )

    return Classification(entry, description, reason)
