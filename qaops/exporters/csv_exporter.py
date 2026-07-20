"""CsvExporter - the test-case table as CSV (ADR-016).

CSV is intentionally lossy: a TestDesignResult is a graph, but CSV is
flat, so this exporter emits the artifact a QA lead actually imports into
a tracker - one row per test case, with list and mapping fields joined
deterministically. The full graph (requirements, scenarios, coverage)
lives in the JSON and Excel exports.

Deterministic: fixed column order, rows in test-case declaration order,
CRLF line terminator per RFC 4180, no timestamps.
"""

import csv
import io
from pathlib import Path

from qaops.exporters._base import format_steps, join_list, join_test_data, to_canonical_dict
from qaops.models import TestDesignResult

COLUMNS = [
    "id",
    "title",
    "scenario_id",
    "requirement_ids",
    "module",
    "feature",
    "objective",
    "priority",
    "test_type",
    "tags",
    "preconditions",
    "test_data",
    "steps",
    "expected_result",
]


class CsvExporter:
    """Serializes the test-case table of a TestDesignResult to CSV."""

    @property
    def format_name(self) -> str:
        return "csv"

    @property
    def file_extension(self) -> str:
        return ".csv"

    def to_csv(self, result: TestDesignResult) -> str:
        data = to_canonical_dict(result)
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\r\n")
        writer.writeheader()
        for tc in data["test_cases"]:
            writer.writerow(
                {
                    "id": tc["id"],
                    "title": tc["title"],
                    "scenario_id": tc["scenario_id"],
                    "requirement_ids": join_list(tc["requirement_ids"]),
                    "module": tc["module"],
                    "feature": tc["feature"],
                    "objective": tc["objective"],
                    "priority": tc["priority"],
                    "test_type": tc["test_type"],
                    "tags": join_list(tc["tags"]),
                    "preconditions": join_list(tc["preconditions"]),
                    "test_data": join_test_data(tc["test_data"]),
                    "steps": format_steps(tc["steps"]),
                    "expected_result": tc["expected_result"],
                }
            )
        return buffer.getvalue()

    def export(self, result: TestDesignResult, output_path: str) -> str:
        # newline="" so csv's own CRLF terminators are not translated again.
        Path(output_path).write_text(self.to_csv(result), encoding="utf-8", newline="")
        return output_path
