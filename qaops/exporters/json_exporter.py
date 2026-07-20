"""JsonExporter - the canonical serialization (ADR-016).

Writes the full TestDesignResult, coverage included, as pretty-printed
JSON. Every other exporter derives its output from the same
`to_canonical_dict`, so JSON is the reference against which the others
are checked. Output is deterministic: declaration-order keys
(sort_keys=False), fixed indentation, trailing newline, no timestamps.
"""

import json
from pathlib import Path

from qaops.exporters._base import to_canonical_dict
from qaops.models import TestDesignResult


class JsonExporter:
    """Serializes a TestDesignResult to canonical JSON."""

    @property
    def format_name(self) -> str:
        return "json"

    @property
    def file_extension(self) -> str:
        return ".json"

    def to_json(self, result: TestDesignResult) -> str:
        """Return the canonical JSON string (no file I/O)."""
        return json.dumps(
            to_canonical_dict(result),
            indent=2,
            ensure_ascii=False,
            sort_keys=False,
        )

    def export(self, result: TestDesignResult, output_path: str) -> str:
        Path(output_path).write_text(self.to_json(result) + "\n", encoding="utf-8")
        return output_path
