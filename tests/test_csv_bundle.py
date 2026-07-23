"""Tests for CsvBundleExporter - the six-file CSV package.

Verifies the exact filename layout, valid CSV structure, per-entity content,
input immutability, determinism, and that it derives from the same canonical
dict as the other exporters (no business-logic duplication)."""

import csv
from pathlib import Path

from qaops.exporters import CsvBundleExporter
from qaops.models import (
    BusinessRule,
    Gap,
    GapReport,
    GapSeverity,
    Priority,
    Requirement,
    Scenario,
    ScenarioCategory,
    TestCase,
    TestDesignResult,
    TestStep,
    TestType,
)
from qaops.pipelines.test_design import CoverageValidator

EXPECTED_FILES = {
    "Requirements.csv",
    "BusinessRules.csv",
    "Scenarios.csv",
    "TestCases.csv",
    "GapAnalysis.csv",
    "Coverage.csv",
}


def sample_result() -> TestDesignResult:
    base = TestDesignResult(
        source_name="prd.pdf",
        requirements=[
            Requirement(
                id="REQ-001",
                title="Login",
                description="User logs in.",
                actors=["User"],
                validations=["email required"],
            ),
            Requirement(id="REQ-002", title="Lockout", description="Locks after 5."),
        ],
        business_rules=[
            BusinessRule(id="BR-001", requirement_id="REQ-002", rule="Locks after 5 attempts."),
        ],
        gap_report=GapReport(
            gaps=[
                Gap(
                    description="Lockout duration unspecified.",
                    severity=GapSeverity.BLOCKER,
                    requirement_id="REQ-002",
                    suggested_question="How long is the lock?",
                ),
                Gap(description="Document-wide ambiguity.", severity=GapSeverity.MINOR),
            ]
        ),
        scenarios=[
            Scenario(
                id="SC-001",
                title="valid login",
                category=ScenarioCategory.POSITIVE,
                requirement_ids=["REQ-001"],
            ),
        ],
        test_cases=[
            TestCase(
                id="TC-001",
                scenario_id="SC-001",
                requirement_ids=["REQ-001"],
                title="Valid login",
                steps=[TestStep(number=1, action="submit", expected="ok")],
                expected_result="dashboard",
                priority=Priority.HIGH,
                test_type=TestType.FUNCTIONAL,
                tags=["smoke"],
            ),
        ],
    )
    return CoverageValidator().run(base)


def _read(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))


class TestFileLayout:
    def test_writes_exactly_the_six_named_files(self, tmp_path: Path) -> None:
        CsvBundleExporter().export_bundle(sample_result(), tmp_path)
        produced = {p.name for p in tmp_path.glob("*.csv")}
        assert produced == EXPECTED_FILES

    def test_returns_paths_written(self, tmp_path: Path) -> None:
        written = CsvBundleExporter().export_bundle(sample_result(), tmp_path)
        assert len(written) == 6
        assert all(Path(p).exists() for p in written)

    def test_creates_output_directory_if_missing(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "reports"
        CsvBundleExporter().export_bundle(sample_result(), target)
        assert (target / "Requirements.csv").exists()


class TestContent:
    def test_requirements_csv(self, tmp_path: Path) -> None:
        CsvBundleExporter().export_bundle(sample_result(), tmp_path)
        rows = _read(tmp_path / "Requirements.csv")
        assert len(rows) == 2
        assert rows[0]["id"] == "REQ-001"
        assert rows[0]["actors"] == "User"
        assert rows[0]["validations"] == "email required"

    def test_business_rules_csv(self, tmp_path: Path) -> None:
        CsvBundleExporter().export_bundle(sample_result(), tmp_path)
        rows = _read(tmp_path / "BusinessRules.csv")
        assert len(rows) == 1
        assert rows[0]["id"] == "BR-001"
        assert rows[0]["requirement_id"] == "REQ-002"

    def test_scenarios_csv(self, tmp_path: Path) -> None:
        CsvBundleExporter().export_bundle(sample_result(), tmp_path)
        rows = _read(tmp_path / "Scenarios.csv")
        assert rows[0]["id"] == "SC-001"
        assert rows[0]["category"] == "positive"
        assert rows[0]["requirement_ids"] == "REQ-001"

    def test_test_cases_csv(self, tmp_path: Path) -> None:
        CsvBundleExporter().export_bundle(sample_result(), tmp_path)
        rows = _read(tmp_path / "TestCases.csv")
        assert rows[0]["id"] == "TC-001"
        assert "1. submit -> ok" in rows[0]["steps"]
        assert rows[0]["tags"] == "smoke"

    def test_gap_analysis_csv(self, tmp_path: Path) -> None:
        CsvBundleExporter().export_bundle(sample_result(), tmp_path)
        rows = _read(tmp_path / "GapAnalysis.csv")
        assert len(rows) == 2
        assert rows[0]["severity"] == "blocker"
        assert rows[0]["requirement_id"] == "REQ-002"
        assert rows[1]["requirement_id"] == ""  # document-wide gap

    def test_coverage_csv_spans_three_entity_types(self, tmp_path: Path) -> None:
        CsvBundleExporter().export_bundle(sample_result(), tmp_path)
        rows = _read(tmp_path / "Coverage.csv")
        entity_types = {r["entity_type"] for r in rows}
        assert entity_types == {"requirement", "business_rule", "scenario"}


class TestSafety:
    def test_input_not_mutated(self, tmp_path: Path) -> None:
        result = sample_result()
        before = result.model_dump_json()
        CsvBundleExporter().export_bundle(result, tmp_path)
        assert result.model_dump_json() == before

    def test_deterministic_across_runs(self, tmp_path: Path) -> None:
        a = tmp_path / "a"
        b = tmp_path / "b"
        result = sample_result()
        CsvBundleExporter().export_bundle(result, a)
        CsvBundleExporter().export_bundle(result, b)
        for name in EXPECTED_FILES:
            assert (a / name).read_bytes() == (b / name).read_bytes()

    def test_format_name(self) -> None:
        assert CsvBundleExporter.format_name == "csv-bundle"


class TestFormatRegistrationConsistency:
    """Every selectable format must be accepted by settings validation.

    Regression: csv-bundle was wired into the CLI dispatch but not into the
    QAOpsSettings validator, so a valid qaops.yaml was rejected. The format
    name is defined in more than one place; this test keeps them in sync.
    """

    def test_settings_accepts_every_cli_format(self) -> None:
        from qaops.cli.registry import EXPORTERS
        from qaops.config import QAOpsSettings

        all_formats = [*sorted(EXPORTERS), CsvBundleExporter.format_name]
        settings = QAOpsSettings(default_export_formats=all_formats)
        assert settings.default_export_formats == all_formats

    def test_settings_accepts_csv_bundle_alone(self) -> None:
        from qaops.config import QAOpsSettings

        settings = QAOpsSettings(default_export_formats=["csv-bundle"])
        assert settings.default_export_formats == ["csv-bundle"]

    def test_settings_still_rejects_genuinely_unknown_format(self) -> None:
        import pytest

        from qaops.config import QAOpsSettings

        with pytest.raises(ValueError, match="Unknown export formats"):
            QAOpsSettings(default_export_formats=["pdf"])
