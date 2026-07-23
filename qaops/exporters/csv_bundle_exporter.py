"""CsvBundleExporter - a six-file CSV package (ADR-016, extended).

Unlike the single-file CsvExporter (which emits only the test-case table),
this writes one CSV per entity in the result - Requirements, BusinessRules,
Scenarios, TestCases, GapAnalysis, Coverage - to an output directory, for a
QA lead importing each table separately into a tracker.

It deliberately does NOT implement the Exporter protocol: that contract is
export(result, file_path) -> str (one file), and this writes several files to
a directory. It is its own writer, dispatched by the CLI as the 'csv-bundle'
format. All data derives from the canonical dict via the same _base helpers
the other exporters use, so no business logic is duplicated. Output is
deterministic: fixed columns, declaration-order rows, RFC-4180 CRLF, no
timestamps.
"""

import csv
import io
from pathlib import Path
from typing import Any

from qaops.exporters._base import format_steps, join_list, join_test_data, to_canonical_dict
from qaops.models import TestDesignResult

# Each entry: output filename -> (column headers, row-builder over the canonical dict).
# Filenames match the requested layout exactly.
REQUIREMENTS_COLUMNS = [
    "id",
    "title",
    "description",
    "actors",
    "inputs",
    "outputs",
    "validations",
    "dependencies",
    "constraints",
    "assumptions",
    "source_excerpt",
]
BUSINESS_RULES_COLUMNS = ["id", "requirement_id", "rule", "source_excerpt"]
SCENARIOS_COLUMNS = ["id", "title", "description", "category", "requirement_ids"]
TEST_CASES_COLUMNS = [
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
GAP_ANALYSIS_COLUMNS = ["requirement_id", "severity", "description", "suggested_question"]
COVERAGE_COLUMNS = ["entity_type", "entity_id", "status", "test_case_ids"]


class CsvBundleExporter:
    """Writes a TestDesignResult as six separate CSV files in a directory."""

    format_name = "csv-bundle"

    def export_bundle(self, result: TestDesignResult, output_dir: Path) -> list[str]:
        """Write all six CSV files into output_dir; return the paths written."""
        data = to_canonical_dict(result)
        output_dir.mkdir(parents=True, exist_ok=True)

        files: list[tuple[str, list[str], list[dict[str, Any]]]] = [
            ("Requirements.csv", REQUIREMENTS_COLUMNS, self._requirement_rows(data)),
            ("BusinessRules.csv", BUSINESS_RULES_COLUMNS, self._business_rule_rows(data)),
            ("Scenarios.csv", SCENARIOS_COLUMNS, self._scenario_rows(data)),
            ("TestCases.csv", TEST_CASES_COLUMNS, self._test_case_rows(data)),
            ("GapAnalysis.csv", GAP_ANALYSIS_COLUMNS, self._gap_rows(data)),
            ("Coverage.csv", COVERAGE_COLUMNS, self._coverage_rows(data)),
        ]

        written: list[str] = []
        for filename, columns, rows in files:
            target = output_dir / filename
            target.write_text(self._render(columns, rows), encoding="utf-8", newline="")
            written.append(str(target))
        return written

    # --- rendering -----------------------------------------------------------

    def _render(self, columns: list[str], rows: list[dict[str, Any]]) -> str:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\r\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return buffer.getvalue()

    # --- row builders (all read from the canonical dict) ---------------------

    def _requirement_rows(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "description": r["description"],
                "actors": join_list(r["actors"]),
                "inputs": join_list(r["inputs"]),
                "outputs": join_list(r["outputs"]),
                "validations": join_list(r["validations"]),
                "dependencies": join_list(r["dependencies"]),
                "constraints": join_list(r["constraints"]),
                "assumptions": join_list(r["assumptions"]),
                "source_excerpt": r["source_excerpt"],
            }
            for r in data["requirements"]
        ]

    def _business_rule_rows(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "id": br["id"],
                "requirement_id": br["requirement_id"],
                "rule": br["rule"],
                "source_excerpt": br["source_excerpt"],
            }
            for br in data["business_rules"]
        ]

    def _scenario_rows(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "id": s["id"],
                "title": s["title"],
                "description": s["description"],
                "category": s["category"],
                "requirement_ids": join_list(s["requirement_ids"]),
            }
            for s in data["scenarios"]
        ]

    def _test_case_rows(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        return [
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
            for tc in data["test_cases"]
        ]

    def _gap_rows(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "requirement_id": g["requirement_id"] or "",
                "severity": g["severity"],
                "description": g["description"],
                "suggested_question": g["suggested_question"],
            }
            for g in data["gap_report"]["gaps"]
        ]

    def _coverage_rows(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        # One flat table across requirement, business-rule, and scenario coverage,
        # distinguished by entity_type - so a reader sees every verdict in one file.
        coverage = data["coverage"]
        rows: list[dict[str, Any]] = []
        for rc in coverage["per_requirement"]:
            rows.append(
                {
                    "entity_type": "requirement",
                    "entity_id": rc["requirement_id"],
                    "status": rc["status"],
                    "test_case_ids": join_list(rc["test_case_ids"]),
                }
            )
        for bc in coverage["per_business_rule"]:
            rows.append(
                {
                    "entity_type": "business_rule",
                    "entity_id": bc["rule_id"],
                    "status": bc["status"],
                    "test_case_ids": join_list(bc["test_case_ids"]),
                }
            )
        for sc in coverage["per_scenario"]:
            rows.append(
                {
                    "entity_type": "scenario",
                    "entity_id": sc["scenario_id"],
                    "status": sc["status"],
                    "test_case_ids": join_list(sc["test_case_ids"]),
                }
            )
        return rows
