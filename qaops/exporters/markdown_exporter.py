"""MarkdownExporter - a human-readable QA report.

Renders the full result as a structured Markdown document: a coverage
summary up front (the QA-review headline), then requirements, gap
report, scenarios, and full test cases with traceability. Derived from
the canonical dict (ADR-016) so it never drifts from the JSON. No
timestamps; identical input yields identical output.
"""

from pathlib import Path
from typing import Any

from qaops.exporters._base import join_list, join_test_data, to_canonical_dict
from qaops.models import TestDesignResult


class MarkdownExporter:
    """Serializes a TestDesignResult to a Markdown report."""

    @property
    def format_name(self) -> str:
        return "markdown"

    @property
    def file_extension(self) -> str:
        return ".md"

    def to_markdown(self, result: TestDesignResult) -> str:
        data = to_canonical_dict(result)
        lines: list[str] = []
        self._header(lines, data)
        self._coverage(lines, data["coverage"])
        self._requirements(lines, data["requirements"])
        self._gaps(lines, data["gap_report"])
        self._scenarios(lines, data["scenarios"])
        self._test_cases(lines, data["test_cases"])
        return "\n".join(lines).rstrip() + "\n"

    def export(self, result: TestDesignResult, output_path: str) -> str:
        Path(output_path).write_text(self.to_markdown(result), encoding="utf-8")
        return output_path

    # --- sections ------------------------------------------------------------

    def _header(self, lines: list[str], data: dict[str, Any]) -> None:
        lines.append(f"# Test Design Report: {data['source_name']}")
        lines.append("")

    def _coverage(self, lines: list[str], coverage: dict[str, Any]) -> None:
        m = coverage["metrics"]
        lines.append("## Coverage Summary")
        lines.append("")
        lines.append("| Metric | Covered | Total | Percentage |")
        lines.append("| --- | --- | --- | --- |")
        lines.append(
            f"| Requirements | {m['covered_requirements']} | {m['total_requirements']} "
            f"| {self._pct(m['covered_requirements'], m['total_requirements'])} |"
        )
        lines.append(
            f"| Business rules | {m['covered_business_rules']} | {m['total_business_rules']} "
            f"| {self._pct(m['covered_business_rules'], m['total_business_rules'])} |"
        )
        lines.append(
            f"| Scenarios | {m['covered_scenarios']} | {m['total_scenarios']} "
            f"| {self._pct(m['covered_scenarios'], m['total_scenarios'])} |"
        )
        lines.append(f"| Test cases | — | {m['total_test_cases']} | — |")
        lines.append("")

        uncovered_reqs = [
            rc["requirement_id"]
            for rc in coverage["per_requirement"]
            if rc["status"] == "uncovered"
        ]
        if uncovered_reqs:
            lines.append(f"**Uncovered requirements:** {join_list(uncovered_reqs)}")
            lines.append("")
        if coverage["invalid_references"]:
            lines.append(
                f"**Invalid references:** {len(coverage['invalid_references'])} (see JSON)"
            )
            lines.append("")
        if coverage["duplicate_pairs"]:
            lines.append("**Suspected duplicate test cases:**")
            lines.append("")
            for dup in coverage["duplicate_pairs"]:
                reason = f" — {dup['reason']}" if dup["reason"] else ""
                lines.append(f"- {dup['test_case_id_a']} / {dup['test_case_id_b']}{reason}")
            lines.append("")

    def _requirements(self, lines: list[str], requirements: list[dict[str, Any]]) -> None:
        lines.append("## Requirements")
        lines.append("")
        for req in requirements:
            lines.append(f"### {req['id']}: {req['title']}")
            lines.append("")
            lines.append(req["description"])
            lines.append("")
            if req["actors"]:
                lines.append(f"- **Actors:** {join_list(req['actors'])}")
            if req["validations"]:
                lines.append(f"- **Validations:** {join_list(req['validations'])}")
            if req["actors"] or req["validations"]:
                lines.append("")

    def _gaps(self, lines: list[str], gap_report: dict[str, Any]) -> None:
        gaps = gap_report["gaps"]
        lines.append(f"## Gap Report ({len(gaps)})")
        lines.append("")
        if not gaps:
            lines.append("_No gaps identified._")
            lines.append("")
            return
        for gap in gaps:
            ref = gap["requirement_id"] or "document-wide"
            lines.append(f"- **[{gap['severity'].upper()}]** ({ref}) {gap['description']}")
            if gap["suggested_question"]:
                lines.append(f"  - _Ask:_ {gap['suggested_question']}")
        lines.append("")

    def _scenarios(self, lines: list[str], scenarios: list[dict[str, Any]]) -> None:
        lines.append(f"## Scenarios ({len(scenarios)})")
        lines.append("")
        for sc in scenarios:
            refs = join_list(sc["requirement_ids"])
            lines.append(f"- **{sc['id']}** ({sc['category']}) {sc['title']} — {refs}")
        lines.append("")

    def _test_cases(self, lines: list[str], test_cases: list[dict[str, Any]]) -> None:
        lines.append(f"## Test Cases ({len(test_cases)})")
        lines.append("")
        for tc in test_cases:
            lines.append(f"### {tc['id']}: {tc['title']}")
            lines.append("")
            lines.append(
                f"- **Scenario:** {tc['scenario_id']} | "
                f"**Requirements:** {join_list(tc['requirement_ids'])}"
            )
            lines.append(f"- **Priority:** {tc['priority']} | **Type:** {tc['test_type']}")
            if tc["objective"]:
                lines.append(f"- **Objective:** {tc['objective']}")
            if tc["preconditions"]:
                lines.append(f"- **Preconditions:** {join_list(tc['preconditions'])}")
            if tc["test_data"]:
                lines.append(f"- **Test data:** {join_test_data(tc['test_data'])}")
            lines.append("")
            lines.append("| # | Action | Expected |")
            lines.append("| --- | --- | --- |")
            for step in tc["steps"]:
                expected = step.get("expected", "") or ""
                lines.append(f"| {step['number']} | {step['action']} | {expected} |")
            lines.append("")
            lines.append(f"**Expected result:** {tc['expected_result']}")
            lines.append("")

    @staticmethod
    def _pct(covered: int, total: int) -> str:
        return f"{round(100.0 * covered / total, 1)}%" if total else "—"
