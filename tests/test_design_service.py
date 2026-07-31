"""Phase 16 tests: the extracted DesignService (ADR-028).

The service holds the orchestration the CLI and API share. These verify it
behaves identically to the old CLI path and that the extraction preserved
ADR-023 workflow safety."""

import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from qaops.config import QAOpsSettings
from qaops.core.errors import ConfigurationError, ExportError
from qaops.llm import MockLLMClient
from qaops.services import DesignService, summarize

TEST_CASES = json.dumps(
    {
        "test_cases": [
            {
                "scenario_id": "SC-001",
                "condition_id": "COND-001",
                "requirement_ids": ["REQ-001"],
                "title": "t",
                "expected_result": "r",
                "steps": [{"action": "a"}],
                "priority": "high",
                "test_type": "functional",
            }
        ]
    }
)


CONDITIONS = json.dumps(
    {
        "conditions": [
            {
                "scenario_id": "SC-001",
                "requirement_ids": ["REQ-001"],
                "business_rule_ids": [],
                "category": "positive",
                "description": "primary condition",
                "rationale": "REQ-001",
                "source_basis": "explicit_requirement",
                "status": "resolved",
                "parameters": {},
                "gap_reference": "",
            }
        ]
    }
)


@pytest.fixture(autouse=True)
def _mock_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "qaops.services.design_service.create_client",
        lambda settings: MockLLMClient([CONDITIONS, TEST_CASES]),
    )


def _settings(tmp_path: Path) -> QAOpsSettings:
    return QAOpsSettings(
        provider="mock", output_dir=tmp_path / "out", default_export_formats=["json"]
    )


class TestDesignService:
    def test_detects_scenarios_and_runs(self, tmp_path: Path) -> None:
        path = tmp_path / "s.csv"
        path.write_text("title,category,requirement_ids\r\nvalid,positive,REQ-001\r\n", newline="")
        outcome = DesignService().run(path, _settings(tmp_path))
        assert outcome.entry_point.value == "scenarios"
        assert outcome.detection is not None
        assert summarize(outcome.result)["test_cases"] == 1

    def test_writes_artifacts(self, tmp_path: Path) -> None:
        path = tmp_path / "s.csv"
        path.write_text("title,category\r\nvalid,positive\r\n", newline="")
        outcome = DesignService().run(path, _settings(tmp_path))
        assert outcome.artifacts
        assert all(a.path.exists() for a in outcome.artifacts)

    def test_explicit_entry_point_overrides_detection(self, tmp_path: Path) -> None:
        path = tmp_path / "s.csv"
        path.write_text("title,category,requirement_ids\r\nvalid,positive,REQ-001\r\n", newline="")
        outcome = DesignService().run(path, _settings(tmp_path), from_="scenarios")
        assert outcome.detection is None  # detection skipped
        assert outcome.entry_point.value == "scenarios"

    def test_progress_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "s.csv"
        path.write_text("title,category\r\nvalid,positive\r\n", newline="")
        lines: list[str] = []
        DesignService().run(path, _settings(tmp_path), report=lines.append)
        assert any("Detected:" in line for line in lines)
        assert any("Done." in line for line in lines)

    def test_missing_file_raises_configuration_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="not found"):
            DesignService().run(tmp_path / "nope.csv", _settings(tmp_path))

    def test_unknown_entry_point_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "s.csv"
        path.write_text("title\r\nx\r\n", newline="")
        with pytest.raises(ConfigurationError, match="Unknown entry point"):
            DesignService().run(path, _settings(tmp_path), from_="magic")

    def test_output_collision_guard_preserved(self, tmp_path: Path) -> None:
        # Reading a file the run would also write must be refused (ADR-023).
        out = tmp_path / "out"
        out.mkdir()
        path = out / "s.json"
        path.write_text('{"scenarios": [{"title": "x", "category": "positive"}]}')
        settings = QAOpsSettings(provider="mock", output_dir=out, default_export_formats=["json"])
        with pytest.raises(ExportError, match="overwrite"):
            DesignService().run(path, settings, from_="scenarios")


class TestSummarize:
    def test_derives_from_domain_result(self, tmp_path: Path) -> None:
        wb = Workbook()
        sheet = wb.active
        sheet.append(["title", "category", "requirement_ids"])
        sheet.append(["valid", "positive", "REQ-001"])
        path = tmp_path / "s.xlsx"
        wb.save(path)
        outcome = DesignService().run(path, _settings(tmp_path))
        summary = summarize(outcome.result)
        assert set(summary) == {
            "requirements",
            "business_rules",
            "scenarios",
            "test_conditions",
            "test_cases",
            "gaps",
            "coverage_percent",
            "requirement_coverage_percent",
            "business_rule_coverage_percent",
            "scenario_coverage_percent",
            "condition_coverage_percent",
            "unresolved_conditions",
            "expansion_truncated",
        }
        # Backward-compatible headline still equals requirement coverage.
        assert summary["coverage_percent"] == summary["requirement_coverage_percent"]
