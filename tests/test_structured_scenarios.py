"""Phase 13 tests: human-authored scenario documents (ADR-024).

XLSX, markdown tables, markdown/TXT lists, header aliasing, and graceful
failure on unstructured prose. Readers are deterministic - no LLM anywhere in
these tests."""

import json
from pathlib import Path

import pytest
from openpyxl import Workbook
from typer.testing import CliRunner

import qaops.cli.app as appmod
from qaops.core.errors import DocumentLoadError
from qaops.entrypoints import parse_scenarios
from qaops.entrypoints.structured_readers import read_markdown_scenarios
from qaops.llm import MockLLMClient
from qaops.models import ScenarioDesignResult

TEST_CASES = json.dumps(
    {
        "test_cases": [
            {
                "scenario_id": "SC-001",
                "condition_id": "COND-001",
                "requirement_ids": ["REQ-001"],
                "title": "login works",
                "expected_result": "dashboard",
                "steps": [{"action": "submit", "expected": "ok"}],
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

runner = CliRunner()


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preflight checks for a provider key before the (mocked) client is built."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def make_xlsx(path: Path, rows: list[list[str]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    workbook.save(path)


class TestXlsxScenarios:
    def test_reads_rows_with_canonical_headers(self, tmp_path: Path) -> None:
        path = tmp_path / "s.xlsx"
        make_xlsx(
            path,
            [
                ["title", "description", "category", "requirement_ids"],
                ["Valid login", "Good creds", "positive", "REQ-001"],
                ["Lockout", "Locks", "boundary_value", "REQ-002"],
            ],
        )
        result = parse_scenarios(path)
        assert isinstance(result, ScenarioDesignResult)
        assert [s.title for s in result.scenarios] == ["Valid login", "Lockout"]
        assert result.scenarios[1].category.value == "boundary_value"

    def test_accepts_human_style_headers(self, tmp_path: Path) -> None:
        path = tmp_path / "s.xlsx"
        make_xlsx(
            path,
            [
                ["Scenario Name", "Details", "Type", "Requirement IDs"],
                ["Valid login", "Good creds", "positive", "REQ-001"],
            ],
        )
        scenario = parse_scenarios(path).scenarios[0]
        assert scenario.title == "Valid login"
        assert scenario.description == "Good creds"
        assert scenario.category.value == "positive"

    def test_splits_multiple_requirement_ids(self, tmp_path: Path) -> None:
        path = tmp_path / "s.xlsx"
        make_xlsx(
            path,
            [
                ["title", "requirement_ids"],
                ["Lockout", "REQ-002; REQ-003, REQ-004"],
            ],
        )
        result = parse_scenarios(path)
        assert len(result.analysis.requirements) == 3

    def test_skips_blank_rows(self, tmp_path: Path) -> None:
        path = tmp_path / "s.xlsx"
        make_xlsx(
            path,
            [
                ["title", "category"],
                ["First", "positive"],
                ["", ""],
                ["Second", "negative"],
            ],
        )
        assert len(parse_scenarios(path).scenarios) == 2

    def test_missing_category_defaults_to_functional(self, tmp_path: Path) -> None:
        path = tmp_path / "s.xlsx"
        make_xlsx(path, [["title"], ["Some scenario"]])
        assert parse_scenarios(path).scenarios[0].category.value == "functional"

    def test_missing_title_column_fails_clearly(self, tmp_path: Path) -> None:
        path = tmp_path / "s.xlsx"
        make_xlsx(path, [["Notes", "Owner"], ["something", "me"]])
        with pytest.raises(DocumentLoadError, match="no recognisable scenario title column"):
            parse_scenarios(path)

    def test_empty_workbook_fails_clearly(self, tmp_path: Path) -> None:
        path = tmp_path / "s.xlsx"
        make_xlsx(path, [])
        with pytest.raises(DocumentLoadError, match="contains no data"):
            parse_scenarios(path)


class TestMarkdownTableScenarios:
    def test_reads_a_pipe_table(self, tmp_path: Path) -> None:
        path = tmp_path / "s.md"
        path.write_text(
            "# Scenarios\n\n"
            "| Scenario | Description | Type | Requirement IDs |\n"
            "| --- | --- | --- | --- |\n"
            "| Valid login | Good creds | positive | REQ-001 |\n"
            "| Lockout | Locks | boundary_value | REQ-002 |\n"
        )
        result = parse_scenarios(path)
        assert [s.title for s in result.scenarios] == ["Valid login", "Lockout"]
        assert result.scenarios[0].category.value == "positive"

    def test_table_wins_over_a_list_in_the_same_document(self, tmp_path: Path) -> None:
        path = tmp_path / "s.md"
        path.write_text(
            "- a bullet that is not a scenario\n\n"
            "| title | category |\n| --- | --- |\n| Real scenario | positive |\n"
        )
        result = parse_scenarios(path)
        assert [s.title for s in result.scenarios] == ["Real scenario"]

    def test_table_without_title_column_is_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "s.md"
        path.write_text("| owner | status |\n| --- | --- |\n| me | draft |\n")
        with pytest.raises(DocumentLoadError, match="No structured scenarios"):
            parse_scenarios(path)


class TestListScenarios:
    def test_bulleted_list(self, tmp_path: Path) -> None:
        path = tmp_path / "s.md"
        path.write_text(
            "# Scenarios\n"
            "- Valid login with correct password (positive) REQ-001\n"
            "- Reject blank email (negative) REQ-002\n"
        )
        result = parse_scenarios(path)
        assert result.scenarios[0].title == "Valid login with correct password"
        assert result.scenarios[0].category.value == "positive"
        assert result.scenarios[1].category.value == "negative"

    def test_numbered_list(self, tmp_path: Path) -> None:
        path = tmp_path / "s.txt"
        path.write_text("1. First scenario REQ-001\n2. Second scenario REQ-002\n")
        assert len(parse_scenarios(path).scenarios) == 2

    def test_requirement_ids_are_stripped_from_the_title(self, tmp_path: Path) -> None:
        path = tmp_path / "s.txt"
        path.write_text("* Search returns results REQ-010\n")
        scenario = parse_scenarios(path).scenarios[0]
        assert scenario.title == "Search returns results"
        assert len(parse_scenarios(path).analysis.requirements) == 1

    def test_item_without_category_defaults_to_functional(self, tmp_path: Path) -> None:
        path = tmp_path / "s.txt"
        path.write_text("- Session times out after 30 minutes REQ-003\n")
        assert parse_scenarios(path).scenarios[0].category.value == "functional"

    def test_unknown_parenthetical_is_left_in_the_title(self, tmp_path: Path) -> None:
        path = tmp_path / "s.txt"
        path.write_text("- Login works (see spec section 4) REQ-001\n")
        assert "(see spec section 4)" in parse_scenarios(path).scenarios[0].title


class TestUnstructuredInput:
    def test_prose_fails_with_actionable_guidance(self, tmp_path: Path) -> None:
        path = tmp_path / "prose.md"
        path.write_text("The system should let users log in. It must be secure and fast.")
        with pytest.raises(DocumentLoadError) as excinfo:
            parse_scenarios(path)
        message = str(excinfo.value)
        assert "No structured scenarios found" in message
        # Points the user at the entry point that does handle prose.
        assert "without --from" in message

    def test_reader_is_deterministic(self, tmp_path: Path) -> None:
        path = tmp_path / "s.md"
        text = "| title | category |\n| --- | --- |\n| A | positive |\n"
        path.write_text(text)
        assert read_markdown_scenarios(path, text) == read_markdown_scenarios(path, text)


class TestUnsupportedFormat:
    def test_message_lists_supported_extensions(self, tmp_path: Path) -> None:
        path = tmp_path / "s.pdf"
        path.write_bytes(b"%PDF-1.4 fake")
        with pytest.raises(DocumentLoadError) as excinfo:
            parse_scenarios(path)
        assert ".xlsx" in str(excinfo.value)
        assert ".md" in str(excinfo.value)


class TestCliIntegration:
    def test_xlsx_scenarios_run_end_to_end(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            "qaops.services.design_service.create_client",
            lambda s: MockLLMClient([CONDITIONS, TEST_CASES]),
        )
        path = tmp_path / "team.xlsx"
        make_xlsx(
            path, [["Scenario", "Type", "Requirement IDs"], ["Valid login", "positive", "REQ-001"]]
        )
        result = runner.invoke(
            appmod.app,
            ["design", str(path), "--from", "scenarios", "-o", str(tmp_path / "out"), "-f", "json"],
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "out" / "team.json").exists()

    def test_markdown_scenarios_run_end_to_end(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            "qaops.services.design_service.create_client",
            lambda s: MockLLMClient([CONDITIONS, TEST_CASES]),
        )
        path = tmp_path / "team.md"
        path.write_text(
            "| title | category | requirement_ids |\n"
            "| --- | --- | --- |\n"
            "| Valid login | positive | REQ-001 |\n"
        )
        result = runner.invoke(
            appmod.app,
            ["design", str(path), "--from", "scenarios", "-o", str(tmp_path / "out"), "-f", "json"],
        )
        assert result.exit_code == 0, result.output

    def test_prose_failure_is_friendly_in_the_cli(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            "qaops.services.design_service.create_client",
            lambda s: MockLLMClient([CONDITIONS, TEST_CASES]),
        )
        path = tmp_path / "prose.md"
        path.write_text("Just some prose about the system.")
        result = runner.invoke(
            appmod.app, ["design", str(path), "--from", "scenarios", "-o", str(tmp_path / "out")]
        )
        assert result.exit_code == 1
        assert "No structured scenarios found" in result.output
        assert "Traceback" not in result.output
