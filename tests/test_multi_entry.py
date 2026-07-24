"""Phase 12 tests: multi-entry pipeline (ADR-022).

Covers parsers (JSON/CSV, validation, ID assignment), PipelineBuilder stage
selection, all three entry points end to end, CLI routing, and that exporters
work regardless of entry point. No pipeline stage, prompt, model, or exporter
is modified by this phase, so the existing suite is the regression check."""

import csv
import io
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import qaops.cli.app as appmod
from qaops.config import QAOpsSettings
from qaops.core.errors import DocumentLoadError
from qaops.entrypoints import (
    EntryPoint,
    build_pipeline_for,
    parse_requirements,
    parse_scenarios,
    stage_names_for,
)
from qaops.exporters import CsvBundleExporter, JsonExporter, MarkdownExporter
from qaops.llm import MockLLMClient, PromptLoader
from qaops.models import (
    RequirementAnalysisResult,
    ScenarioDesignResult,
    TestDesignResult,
)

RULES = json.dumps(
    {"rules": [{"requirement_id": "REQ-001", "rule": "a rule", "source_excerpt": ""}]}
)
GAPS = json.dumps({"gaps": []})
SCENARIOS = json.dumps(
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
)
TEST_CASES = json.dumps(
    {
        "test_cases": [
            {
                "scenario_id": "SC-001",
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

runner = CliRunner()


@pytest.fixture
def settings(tmp_path: Path) -> QAOpsSettings:
    return QAOpsSettings(output_dir=tmp_path / "out")


@pytest.fixture
def prompts() -> PromptLoader:
    return PromptLoader()


class TestRequirementParser:
    def test_json_object_with_requirements_key(self, tmp_path: Path) -> None:
        path = tmp_path / "reqs.json"
        path.write_text(
            json.dumps(
                {
                    "requirements": [
                        {"title": "Login", "description": "Users log in.", "actors": ["User"]},
                        {"title": "Lockout", "description": "Locks after 5."},
                    ]
                }
            )
        )
        analysis = parse_requirements(path)
        assert isinstance(analysis, RequirementAnalysisResult)
        assert [r.id for r in analysis.requirements] == ["REQ-001", "REQ-002"]
        assert analysis.requirements[0].actors == ["User"]

    def test_json_bare_list(self, tmp_path: Path) -> None:
        path = tmp_path / "reqs.json"
        path.write_text(json.dumps([{"title": "Login", "description": "d"}]))
        assert len(parse_requirements(path).requirements) == 1

    def test_csv_with_joined_list_columns(self, tmp_path: Path) -> None:
        path = tmp_path / "reqs.csv"
        path.write_text(
            "title,description,actors,validations\r\n"
            "Login,Users log in,User; Admin,email required; password required\r\n",
            newline="",
        )
        analysis = parse_requirements(path)
        assert analysis.requirements[0].actors == ["User", "Admin"]
        assert analysis.requirements[0].validations == [
            "email required",
            "password required",
        ]

    def test_ids_are_reassigned_not_trusted(self, tmp_path: Path) -> None:
        path = tmp_path / "reqs.json"
        path.write_text(json.dumps([{"id": "REQ-999", "title": "A", "description": "d"}]))
        assert parse_requirements(path).requirements[0].id == "REQ-001"

    def test_description_defaults_to_title(self, tmp_path: Path) -> None:
        path = tmp_path / "reqs.json"
        path.write_text(json.dumps([{"title": "Just a title"}]))
        assert parse_requirements(path).requirements[0].description == "Just a title"

    def test_rejects_unsupported_extension(self, tmp_path: Path) -> None:
        path = tmp_path / "reqs.txt"
        path.write_text("nope")
        with pytest.raises(DocumentLoadError, match="must be .json or .csv"):
            parse_requirements(path)

    def test_rejects_invalid_json(self, tmp_path: Path) -> None:
        path = tmp_path / "reqs.json"
        path.write_text("{not json")
        with pytest.raises(DocumentLoadError, match="not valid JSON"):
            parse_requirements(path)

    def test_rejects_empty_title(self, tmp_path: Path) -> None:
        path = tmp_path / "reqs.json"
        path.write_text(json.dumps([{"title": "  ", "description": "d"}]))
        with pytest.raises(DocumentLoadError, match="empty title"):
            parse_requirements(path)

    def test_rejects_csv_missing_title_column(self, tmp_path: Path) -> None:
        path = tmp_path / "reqs.csv"
        path.write_text("description\r\nsomething\r\n", newline="")
        with pytest.raises(DocumentLoadError, match="missing required column"):
            parse_requirements(path)


class TestScenarioParser:
    def test_csv_scenarios_with_synthesized_requirements(self, tmp_path: Path) -> None:
        path = tmp_path / "scen.csv"
        path.write_text(
            "title,description,category,requirement_ids\r\n"
            "valid login,d,positive,REQ-001\r\n"
            "lockout,d,boundary_value,REQ-002\r\n",
            newline="",
        )
        design = parse_scenarios(path)
        assert isinstance(design, ScenarioDesignResult)
        assert [s.id for s in design.scenarios] == ["SC-001", "SC-002"]
        # Requirements referenced but not defined are synthesized, so the
        # downstream reference validation in TestCaseGenerator can succeed.
        assert len(design.analysis.requirements) == 2

    def test_json_with_supplied_requirements(self, tmp_path: Path) -> None:
        path = tmp_path / "scen.json"
        path.write_text(
            json.dumps(
                {
                    "requirements": [
                        {"id": "R1", "title": "Login", "description": "Users log in."}
                    ],
                    "scenarios": [
                        {"title": "valid login", "category": "positive", "requirement_ids": ["R1"]}
                    ],
                }
            )
        )
        design = parse_scenarios(path)
        assert design.analysis.requirements[0].title == "Login"
        # The file's own ID is remapped to a canonical one.
        assert design.scenarios[0].requirement_ids == [design.analysis.requirements[0].id]

    def test_scenario_without_requirement_ids_gets_placeholder(self, tmp_path: Path) -> None:
        path = tmp_path / "scen.csv"
        path.write_text("title,category\r\nsome scenario,positive\r\n", newline="")
        design = parse_scenarios(path)
        assert len(design.analysis.requirements) == 1
        assert design.scenarios[0].requirement_ids == [design.analysis.requirements[0].id]

    def test_category_defaults_to_functional(self, tmp_path: Path) -> None:
        path = tmp_path / "scen.csv"
        path.write_text("title\r\nsome scenario\r\n", newline="")
        assert parse_scenarios(path).scenarios[0].category.value == "functional"

    def test_rejects_unknown_category(self, tmp_path: Path) -> None:
        path = tmp_path / "scen.csv"
        path.write_text("title,category\r\nx,teleportation\r\n", newline="")
        with pytest.raises(DocumentLoadError, match="Unknown scenario category"):
            parse_scenarios(path)

    def test_rejects_empty_title(self, tmp_path: Path) -> None:
        path = tmp_path / "scen.json"
        path.write_text(json.dumps([{"title": "", "category": "positive"}]))
        with pytest.raises(DocumentLoadError, match="empty title"):
            parse_scenarios(path)

    def test_scenario_ids_are_reassigned(self, tmp_path: Path) -> None:
        path = tmp_path / "scen.json"
        path.write_text(json.dumps([{"id": "SC-999", "title": "x", "category": "positive"}]))
        assert parse_scenarios(path).scenarios[0].id == "SC-001"


class TestPipelineBuilder:
    def test_document_entry_builds_all_six_stages(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        pipeline = build_pipeline_for(EntryPoint.DOCUMENT, MockLLMClient([]), prompts, settings)
        assert pipeline.stage_names == stage_names_for(EntryPoint.DOCUMENT)
        assert len(pipeline.stage_names) == 6

    def test_requirements_entry_skips_the_analyzer(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        pipeline = build_pipeline_for(EntryPoint.REQUIREMENTS, MockLLMClient([]), prompts, settings)
        assert pipeline.stage_names[0] == "business_rule_extractor"
        assert "requirement_analyzer" not in pipeline.stage_names

    def test_scenarios_entry_runs_only_cases_and_coverage(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        pipeline = build_pipeline_for(EntryPoint.SCENARIOS, MockLLMClient([]), prompts, settings)
        assert pipeline.stage_names == ["test_case_generator", "coverage_validator"]


class TestEntryPointsEndToEnd:
    def test_requirements_entry_produces_test_design_result(
        self, settings: QAOpsSettings, prompts: PromptLoader, tmp_path: Path
    ) -> None:
        path = tmp_path / "reqs.json"
        path.write_text(json.dumps([{"title": "Login", "description": "Users log in."}]))
        analysis = parse_requirements(path)
        client = MockLLMClient([RULES, GAPS, SCENARIOS, TEST_CASES])
        result = build_pipeline_for(EntryPoint.REQUIREMENTS, client, prompts, settings).run(
            analysis
        )
        assert isinstance(result, TestDesignResult)
        assert len(result.test_cases) == 1
        assert client.call_count == 4  # analyzer skipped

    def test_scenarios_entry_makes_a_single_llm_call(
        self, settings: QAOpsSettings, prompts: PromptLoader, tmp_path: Path
    ) -> None:
        path = tmp_path / "scen.csv"
        path.write_text(
            "title,description,category,requirement_ids\r\nvalid login,d,positive,REQ-001\r\n",
            newline="",
        )
        design = parse_scenarios(path)
        client = MockLLMClient([TEST_CASES])
        result = build_pipeline_for(EntryPoint.SCENARIOS, client, prompts, settings).run(design)
        assert isinstance(result, TestDesignResult)
        assert client.call_count == 1  # only test-case generation
        assert result.coverage.metrics.scenario_coverage_pct == 100.0

    def test_stages_are_unaware_of_the_route(
        self, settings: QAOpsSettings, prompts: PromptLoader, tmp_path: Path
    ) -> None:
        # The same TestCaseGenerator handles scenarios from either route and
        # produces the same model type.
        path = tmp_path / "scen.csv"
        path.write_text(
            "title,description,category,requirement_ids\r\nvalid login,d,positive,REQ-001\r\n",
            newline="",
        )
        design = parse_scenarios(path)
        result = build_pipeline_for(
            EntryPoint.SCENARIOS, MockLLMClient([TEST_CASES]), prompts, settings
        ).run(design)
        assert isinstance(result, TestDesignResult)
        assert result.test_cases[0].id == "TC-001"


class TestExportersAcrossEntryPoints:
    def test_all_exporters_work_from_scenario_entry(
        self, settings: QAOpsSettings, prompts: PromptLoader, tmp_path: Path
    ) -> None:
        path = tmp_path / "scen.csv"
        path.write_text(
            "title,description,category,requirement_ids\r\nvalid login,d,positive,REQ-001\r\n",
            newline="",
        )
        design = parse_scenarios(path)
        result = build_pipeline_for(
            EntryPoint.SCENARIOS, MockLLMClient([TEST_CASES]), prompts, settings
        ).run(design)
        assert isinstance(result, TestDesignResult)

        out = tmp_path / "reports"
        out.mkdir()
        JsonExporter().export(result, str(out / "r.json"))
        MarkdownExporter().export(result, str(out / "r.md"))
        CsvBundleExporter().export_bundle(result, out)
        assert (out / "r.json").exists()
        assert (out / "r.md").exists()
        assert (out / "TestCases.csv").exists()
        rows = list(csv.DictReader((out / "TestCases.csv").read_text().splitlines()))
        assert rows[0]["id"] == "TC-001"


class TestCliRouting:
    def _client(self, monkeypatch: pytest.MonkeyPatch, responses: list[str]) -> MockLLMClient:
        client = MockLLMClient(list(responses))
        monkeypatch.setattr(appmod, "create_client", lambda settings: client)
        return client

    def test_from_requirements(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self._client(monkeypatch, [RULES, GAPS, SCENARIOS, TEST_CASES])
        path = tmp_path / "reqs.json"
        path.write_text(json.dumps([{"title": "Login", "description": "Users log in."}]))
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
        assert "requirements)" in result.output
        assert "business_rule_extractor" in result.output

    def test_from_scenarios(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self._client(monkeypatch, [TEST_CASES])
        path = tmp_path / "scen.csv"
        path.write_text(
            "title,description,category,requirement_ids\r\nvalid login,d,positive,REQ-001\r\n",
            newline="",
        )
        result = runner.invoke(
            appmod.app,
            ["design", str(path), "--from", "scenarios", "-o", str(tmp_path / "o"), "-f", "json"],
        )
        assert result.exit_code == 0, result.output
        assert "test_case_generator -> coverage_validator" in result.output

    def test_unknown_entry_point_is_friendly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._client(monkeypatch, [])
        path = tmp_path / "x.json"
        path.write_text("[]")
        result = runner.invoke(
            appmod.app, ["design", str(path), "--from", "magic", "-o", str(tmp_path)]
        )
        assert result.exit_code == 1
        assert "Unknown entry point" in result.output
        assert "Traceback" not in result.output

    def test_default_entry_point_is_document(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        analyzer = json.dumps(
            {"requirements": [{"title": "Login", "description": "Users log in."}]}
        )
        self._client(monkeypatch, [analyzer, RULES, GAPS, SCENARIOS, TEST_CASES])
        path = tmp_path / "doc.md"
        path.write_text("# PRD\nUsers log in with email and password.")
        result = runner.invoke(
            appmod.app, ["design", str(path), "-o", str(tmp_path / "o"), "-f", "json"]
        )
        assert result.exit_code == 0, result.output
        assert "characters)" in result.output  # document route reports characters
        assert "requirement_analyzer" in result.output


def _roundtrip_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))
