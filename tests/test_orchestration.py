"""Phase 14 tests: automatic workflow selection (ADR-025).

Covers deterministic input classification across every supported format,
pre-flight validation, automatic entry-point selection in the CLI, and that an
explicit --from still overrides detection. No LLM is used for classification,
so none of these tests need a model beyond the scripted MockLLMClient."""

import json
from pathlib import Path

import pytest
from openpyxl import Workbook
from typer.testing import CliRunner

import qaops.cli.app as appmod
from qaops.config import QAOpsSettings
from qaops.entrypoints import EntryPoint, classify_input, format_issues, preflight
from qaops.llm import MockLLMClient

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
                "description": "valid login accepted",
                "rationale": "REQ-001",
                "source_basis": "explicit_requirement",
                "status": "resolved",
                "parameters": {},
                "gap_reference": "",
            }
        ]
    }
)
DOWNSTREAM = [
    json.dumps({"rules": [{"requirement_id": "REQ-001", "rule": "r", "source_excerpt": ""}]}),
    json.dumps({"gaps": []}),
    json.dumps(
        {
            "scenarios": [
                {
                    "title": "valid login",
                    "description": "d",
                    "category": "positive",
                    "requirement_ids": ["REQ-001"],
                }
            ]
        }
    ),
    CONDITIONS,
    TEST_CASES,
]

runner = CliRunner()


@pytest.fixture(autouse=True)
def api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def make_xlsx(path: Path, rows: list[list[str]]) -> None:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    workbook.save(path)


class TestExtensionClassification:
    def test_pdf_is_a_document(self, tmp_path: Path) -> None:
        path = tmp_path / "PRD.pdf"
        path.write_bytes(b"%PDF-1.4 fake")
        assert classify_input(path).entry_point is EntryPoint.DOCUMENT

    def test_docx_is_a_document(self, tmp_path: Path) -> None:
        path = tmp_path / "PRD.docx"
        path.write_bytes(b"PK\x03\x04")
        assert classify_input(path).entry_point is EntryPoint.DOCUMENT

    def test_xlsx_is_scenarios(self, tmp_path: Path) -> None:
        path = tmp_path / "Scenarios.xlsx"
        make_xlsx(path, [["title"], ["Valid login"]])
        assert classify_input(path).entry_point is EntryPoint.SCENARIOS

    def test_unknown_extension_defaults_to_document(self, tmp_path: Path) -> None:
        # The document route's ingestion layer then raises its own precise
        # unsupported-format error.
        path = tmp_path / "notes.rtf"
        path.write_text("something")
        assert classify_input(path).entry_point is EntryPoint.DOCUMENT


class TestCsvClassification:
    def test_scenario_columns_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "Scenarios.csv"
        path.write_text("title,category,requirement_ids\r\nvalid,positive,REQ-001\r\n", newline="")
        result = classify_input(path)
        assert result.entry_point is EntryPoint.SCENARIOS
        assert "category" in result.reason

    def test_requirement_columns_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "Requirements.csv"
        path.write_text(
            "title,description,actors,validations\r\nLogin,d,User,email\r\n", newline=""
        )
        result = classify_input(path)
        assert result.entry_point is EntryPoint.REQUIREMENTS

    def test_bare_table_defaults_to_requirements(self, tmp_path: Path) -> None:
        path = tmp_path / "data.csv"
        path.write_text("title,description\r\nLogin,Users log in\r\n", newline="")
        assert classify_input(path).entry_point is EntryPoint.REQUIREMENTS

    def test_human_style_headers_are_recognised(self, tmp_path: Path) -> None:
        path = tmp_path / "sheet.csv"
        path.write_text(
            "Scenario Name,Type,Requirement IDs\r\nvalid,positive,REQ-001\r\n", newline=""
        )
        assert classify_input(path).entry_point is EntryPoint.SCENARIOS


class TestJsonClassification:
    def test_scenarios_key(self, tmp_path: Path) -> None:
        path = tmp_path / "x.json"
        path.write_text(json.dumps({"scenarios": [{"title": "a", "category": "positive"}]}))
        assert classify_input(path).entry_point is EntryPoint.SCENARIOS

    def test_requirements_key(self, tmp_path: Path) -> None:
        path = tmp_path / "x.json"
        path.write_text(json.dumps({"requirements": [{"title": "a", "description": "d"}]}))
        assert classify_input(path).entry_point is EntryPoint.REQUIREMENTS

    def test_bare_list_with_scenario_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "x.json"
        path.write_text(json.dumps([{"title": "a", "category": "positive"}]))
        assert classify_input(path).entry_point is EntryPoint.SCENARIOS

    def test_malformed_json_does_not_crash(self, tmp_path: Path) -> None:
        path = tmp_path / "x.json"
        path.write_text("{not json")
        assert classify_input(path).entry_point is EntryPoint.REQUIREMENTS


class TestTextualClassification:
    def test_prose_markdown_is_a_document(self, tmp_path: Path) -> None:
        path = tmp_path / "PRD.md"
        path.write_text("# Widgets\n\nThe system shall display widgets based on activity.")
        result = classify_input(path)
        assert result.entry_point is EntryPoint.DOCUMENT
        assert "prose" in result.reason

    def test_markdown_table_is_scenarios(self, tmp_path: Path) -> None:
        path = tmp_path / "s.md"
        path.write_text("| title | category |\n| --- | --- |\n| Valid login | positive |\n")
        assert classify_input(path).entry_point is EntryPoint.SCENARIOS

    def test_scenario_list_is_scenarios(self, tmp_path: Path) -> None:
        path = tmp_path / "s.txt"
        path.write_text(
            "- Valid login (positive) REQ-001\n- Reject blank email (negative) REQ-002\n"
        )
        assert classify_input(path).entry_point is EntryPoint.SCENARIOS

    def test_deterministic(self, tmp_path: Path) -> None:
        path = tmp_path / "s.md"
        path.write_text("| title | category |\n| --- | --- |\n| A | positive |\n")
        assert classify_input(path) == classify_input(path)


class TestPreflight:
    def test_missing_file(self, tmp_path: Path) -> None:
        issues = preflight(tmp_path / "nope.csv", QAOpsSettings(), EntryPoint.REQUIREMENTS)
        assert len(issues) == 1
        assert "not found" in issues[0].problem

    def test_directory_input(self, tmp_path: Path) -> None:
        issues = preflight(tmp_path, QAOpsSettings(), EntryPoint.DOCUMENT)
        assert "is a directory" in issues[0].problem

    def test_missing_api_key(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        path = tmp_path / "r.csv"
        path.write_text("title\r\nLogin\r\n", newline="")
        issues = preflight(path, QAOpsSettings(provider="anthropic"), EntryPoint.REQUIREMENTS)
        assert any("No API key" in issue.problem for issue in issues)
        assert any("ANTHROPIC_API_KEY" in issue.fix for issue in issues)

    def test_gemini_accepts_either_key_variable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "k")
        path = tmp_path / "r.csv"
        path.write_text("title\r\nLogin\r\n", newline="")
        issues = preflight(path, QAOpsSettings(provider="gemini"), EntryPoint.REQUIREMENTS)
        assert not any("No API key" in issue.problem for issue in issues)

    def test_mock_provider_needs_no_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        path = tmp_path / "r.csv"
        path.write_text("title\r\nLogin\r\n", newline="")
        assert preflight(path, QAOpsSettings(provider="mock"), EntryPoint.REQUIREMENTS) == []

    def test_clean_run_has_no_issues(self, tmp_path: Path) -> None:
        path = tmp_path / "r.csv"
        path.write_text("title\r\nLogin\r\n", newline="")
        assert preflight(path, QAOpsSettings(), EntryPoint.REQUIREMENTS) == []

    def test_format_issues_is_actionable(self, tmp_path: Path) -> None:
        issues = preflight(tmp_path / "nope.csv", QAOpsSettings(), EntryPoint.DOCUMENT)
        message = format_issues(issues)
        assert "To fix:" in message


class TestCliAutoDetection:
    def _client(self, monkeypatch: pytest.MonkeyPatch, responses: list[str]) -> None:
        monkeypatch.setattr(
            "qaops.services.design_service.create_client",
            lambda settings: MockLLMClient(list(responses)),
        )

    def test_requirements_csv_without_from(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._client(monkeypatch, DOWNSTREAM)
        path = tmp_path / "Requirements.csv"
        path.write_text("title,description,actors\r\nLogin,Users log in,User\r\n", newline="")
        result = runner.invoke(
            appmod.app, ["design", str(path), "-o", str(tmp_path / "o"), "-f", "json"]
        )
        assert result.exit_code == 0, result.output
        assert "Detected: requirements table" in result.output
        assert "business_rule_extractor" in result.output

    def test_scenarios_xlsx_without_from(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._client(monkeypatch, [CONDITIONS, TEST_CASES])
        path = tmp_path / "Scenarios.xlsx"
        make_xlsx(
            path, [["Scenario", "Type", "Requirement IDs"], ["Valid login", "positive", "REQ-001"]]
        )
        result = runner.invoke(
            appmod.app, ["design", str(path), "-o", str(tmp_path / "o"), "-f", "json"]
        )
        assert result.exit_code == 0, result.output
        assert "Detected: scenario spreadsheet" in result.output
        assert "test_case_generator -> coverage_validator" in result.output

    def test_prose_document_without_from(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        analyzer = json.dumps(
            {"requirements": [{"title": "Login", "description": "Users log in."}]}
        )
        self._client(monkeypatch, [analyzer, *DOWNSTREAM])
        path = tmp_path / "PRD.md"
        path.write_text("# PRD\n\nUsers log in with email and password to reach the dashboard.")
        result = runner.invoke(
            appmod.app, ["design", str(path), "-o", str(tmp_path / "o"), "-f", "json"]
        )
        assert result.exit_code == 0, result.output
        assert "Detected: requirement document" in result.output
        assert "requirement_analyzer" in result.output

    def test_explicit_from_overrides_detection(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A scenario-shaped CSV forced down the requirements route.
        self._client(monkeypatch, DOWNSTREAM)
        path = tmp_path / "Scenarios.csv"
        path.write_text("title,category,requirement_ids\r\nvalid,positive,REQ-001\r\n", newline="")
        result = runner.invoke(
            appmod.app,
            [
                "design",
                str(path),
                "--from",
                "requirements",
                "-o",
                str(tmp_path / "o"),
                "-f",
                "json",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Detected:" not in result.output  # detection skipped
        assert "business_rule_extractor" in result.output

    def test_unknown_from_still_rejected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._client(monkeypatch, [])
        path = tmp_path / "x.csv"
        path.write_text("title\r\nLogin\r\n", newline="")
        result = runner.invoke(
            appmod.app, ["design", str(path), "--from", "magic", "-o", str(tmp_path / "o")]
        )
        assert result.exit_code == 1
        assert "Unknown entry point" in result.output

    def test_missing_api_key_is_caught_before_any_llm_call(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        def explode(settings: object) -> None:
            raise AssertionError("create_client must not be reached in preflight failure")

        monkeypatch.setattr("qaops.services.design_service.create_client", explode)
        path = tmp_path / "r.csv"
        path.write_text("title\r\nLogin\r\n", newline="")
        result = runner.invoke(
            appmod.app, ["design", str(path), "-o", str(tmp_path / "o"), "-f", "json"]
        )
        assert result.exit_code == 1
        assert "No API key" in result.output
        assert "Traceback" not in result.output


class TestPipelineBuilderReuse:
    def test_orchestration_uses_the_existing_builder(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # The CLI must delegate to build_pipeline_for, never construct stages.
        from qaops.entrypoints import builder

        seen: list[EntryPoint] = []
        original = builder.build_pipeline_for

        def spy(entry_point: EntryPoint, *args: object, **kwargs: object) -> object:
            seen.append(entry_point)
            return original(entry_point, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr("qaops.services.design_service.build_pipeline_for", spy)
        monkeypatch.setattr(
            "qaops.services.design_service.create_client",
            lambda s: MockLLMClient([CONDITIONS, TEST_CASES]),
        )
        path = tmp_path / "Scenarios.xlsx"
        make_xlsx(path, [["title", "category"], ["Valid login", "positive"]])
        runner.invoke(appmod.app, ["design", str(path), "-o", str(tmp_path / "o"), "-f", "json"])
        assert seen == [EntryPoint.SCENARIOS]


class TestProseWithListsIsNotScenarios:
    """Regression: a PRD with numbered acceptance criteria is a document.

    The first classifier treated any bulleted or numbered list as a scenario
    list, which sent `examples/login.md` - a prose PRD with numbered criteria -
    down the scenario route, where it failed. List items now only count as
    scenarios when explicitly marked with requirement references or category
    tags, which prose criteria are not.
    """

    def test_golden_prd_with_numbered_criteria(self) -> None:
        examples = Path(__file__).resolve().parent.parent / "examples"
        for name in ("login.md", "checkout.md", "video_playback.md", "fund_transfer.md"):
            classification = classify_input(examples / name)
            assert classification.entry_point is EntryPoint.DOCUMENT, name

    def test_prose_with_a_stray_requirement_reference(self, tmp_path: Path) -> None:
        path = tmp_path / "notes.md"
        path.write_text("# PRD\n- see REQ-001 for details\n- other notes\n- more notes\n")
        assert classify_input(path).entry_point is EntryPoint.DOCUMENT

    def test_marked_scenario_list_still_detected(self, tmp_path: Path) -> None:
        path = tmp_path / "s.md"
        path.write_text("- Valid login (positive) REQ-001\n- Blank email (negative) REQ-002\n")
        assert classify_input(path).entry_point is EntryPoint.SCENARIOS

    def test_corrupt_spreadsheet_fails_gracefully(self, tmp_path: Path) -> None:
        from qaops.core.errors import DocumentLoadError
        from qaops.entrypoints import parse_scenarios

        path = tmp_path / "fake.xlsx"
        path.write_text("not really a spreadsheet")
        with pytest.raises(DocumentLoadError, match="valid .xlsx workbook"):
            parse_scenarios(path)
