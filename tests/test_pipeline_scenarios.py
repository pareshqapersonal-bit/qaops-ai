"""Phase 3 tests: ScenarioGenerator against MockLLMClient.

Deterministic responsibilities under test: SC-* ID assignment,
reference verification, duplicate rejection (ADR-012), wire-to-domain
mapping across all QA technique categories, and the composed 4-stage
pipeline against a golden example (ADR-008)."""

import json
from pathlib import Path

import pytest

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.llm import MockLLMClient, PromptLoader
from qaops.models import (
    RequirementAnalysisResult,
    RequirementInput,
    ScenarioCategory,
    ScenarioDesignResult,
)
from qaops.pipelines.test_design import (
    RequirementAnalyzer,
    ScenarioGenerator,
    build_scenario_pipeline,
)

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

ANALYZER_RESPONSE = json.dumps(
    {
        "requirements": [
            {
                "title": "Login with valid credentials",
                "description": "A registered user logs in with a correct email and password.",
                "source_excerpt": "entering a correct email and password combination",
                "actors": ["Registered user"],
            },
            {
                "title": "Account lockout",
                "description": "The account locks after 5 consecutive failed attempts.",
                "source_excerpt": "After 5 consecutive failed attempts",
            },
        ]
    }
)

RULES_RESPONSE = json.dumps(
    {
        "rules": [
            {
                "requirement_id": "REQ-002",
                "rule": "The account locks after 5 consecutive failed login attempts.",
                "source_excerpt": "After 5 consecutive failed attempts",
            }
        ]
    }
)

GAPS_RESPONSE = json.dumps({"gaps": []})


def _scenario(title: str, category: str, req_ids: list[str]) -> dict[str, object]:
    return {
        "title": title,
        "description": f"Verifies: {title}.",
        "category": category,
        "requirement_ids": req_ids,
    }


TECHNIQUE_SCENARIOS = [
    _scenario("Successful login redirects to dashboard", "functional", ["REQ-001"]),
    _scenario("Login with a freshly registered valid account", "positive", ["REQ-001"]),
    _scenario("Login rejected for wrong password", "negative", ["REQ-001"]),
    _scenario("Fifth consecutive failed attempt locks the account", "boundary_value", ["REQ-002"]),
    _scenario("Fourth consecutive failed attempt does not lock", "boundary_value", ["REQ-002"]),
    _scenario(
        "One representative invalid email format is rejected", "equivalence_partition", ["REQ-001"]
    ),
    _scenario("Empty email and password fields are rejected", "input_validation", ["REQ-001"]),
    _scenario(
        "Generic error message does not reveal which field was wrong", "error_handling", ["REQ-001"]
    ),
    _scenario("Locked account cannot log in with correct credentials", "permission", ["REQ-002"]),
    _scenario("Account transitions from active to locked state", "state_transition", ["REQ-002"]),
    _scenario(
        "Failed-attempt counter and login flow interact correctly",
        "integration",
        ["REQ-001", "REQ-002"],
    ),
]

SCENARIOS_RESPONSE = json.dumps({"scenarios": TECHNIQUE_SCENARIOS})


@pytest.fixture
def settings(tmp_path: Path) -> QAOpsSettings:
    return QAOpsSettings(output_dir=tmp_path / "out")


@pytest.fixture
def prompts() -> PromptLoader:
    return PromptLoader()


def login_input() -> RequirementInput:
    return RequirementInput(
        text=(EXAMPLES_DIR / "login.md").read_text(encoding="utf-8"), source_name="login.md"
    )


@pytest.fixture
def analysis(settings: QAOpsSettings, prompts: PromptLoader) -> RequirementAnalysisResult:
    stage = RequirementAnalyzer(MockLLMClient([ANALYZER_RESPONSE]), prompts, settings)
    return stage.run(login_input())


class TestScenarioGenerator:
    def test_assigns_sequential_sc_ids_and_maps_all_categories(
        self, settings: QAOpsSettings, prompts: PromptLoader, analysis: RequirementAnalysisResult
    ) -> None:
        mock = MockLLMClient([SCENARIOS_RESPONSE])
        result = ScenarioGenerator(mock, prompts, settings).run(analysis)

        assert isinstance(result, ScenarioDesignResult)
        assert [s.id for s in result.scenarios][:3] == ["SC-001", "SC-002", "SC-003"]
        assert len(result.scenarios) == len(TECHNIQUE_SCENARIOS)

        covered = {s.category for s in result.scenarios}
        expected = {
            ScenarioCategory.FUNCTIONAL,
            ScenarioCategory.POSITIVE,
            ScenarioCategory.NEGATIVE,
            ScenarioCategory.BOUNDARY_VALUE,
            ScenarioCategory.EQUIVALENCE_PARTITION,
            ScenarioCategory.INPUT_VALIDATION,
            ScenarioCategory.ERROR_HANDLING,
            ScenarioCategory.PERMISSION,
            ScenarioCategory.STATE_TRANSITION,
            ScenarioCategory.INTEGRATION,
        }
        assert expected <= covered

    def test_analysis_is_composed_untouched(
        self, settings: QAOpsSettings, prompts: PromptLoader, analysis: RequirementAnalysisResult
    ) -> None:
        mock = MockLLMClient([SCENARIOS_RESPONSE])
        result = ScenarioGenerator(mock, prompts, settings).run(analysis)
        assert result.analysis == analysis  # composition, not mutation

    def test_prompt_contains_requirements_rules_and_instructions(
        self, settings: QAOpsSettings, prompts: PromptLoader, analysis: RequirementAnalysisResult
    ) -> None:
        from qaops.pipelines.test_design import BusinessRuleExtractor

        enriched = BusinessRuleExtractor(MockLLMClient([RULES_RESPONSE]), prompts, settings).run(
            analysis
        )
        mock = MockLLMClient([SCENARIOS_RESPONSE])
        ScenarioGenerator(mock, prompts, settings).run(enriched)
        content = mock.requests[0].messages[0].content
        assert "REQ-001" in content and "REQ-002" in content
        assert "5 consecutive failed login attempts" in content  # rules included
        assert "Do NOT generate duplicate scenarios" in content
        assert "never invent IDs" in content

    def test_unknown_requirement_reference_is_a_stage_error(
        self, settings: QAOpsSettings, prompts: PromptLoader, analysis: RequirementAnalysisResult
    ) -> None:
        bad = json.dumps({"scenarios": [_scenario("x", "negative", ["REQ-042"])]})
        with pytest.raises(StageError, match="REQ-042"):
            ScenarioGenerator(MockLLMClient([bad]), prompts, settings).run(analysis)

    def test_invalid_category_triggers_repair_retry(
        self, settings: QAOpsSettings, prompts: PromptLoader, analysis: RequirementAnalysisResult
    ) -> None:
        bad = json.dumps({"scenarios": [_scenario("x", "smoke", ["REQ-001"])]})
        mock = MockLLMClient([bad, SCENARIOS_RESPONSE])
        result = ScenarioGenerator(mock, prompts, settings).run(analysis)
        assert mock.call_count == 2  # first response failed enum validation
        assert len(result.scenarios) == len(TECHNIQUE_SCENARIOS)

    def test_exact_duplicates_are_a_stage_error(
        self, settings: QAOpsSettings, prompts: PromptLoader, analysis: RequirementAnalysisResult
    ) -> None:
        dup = json.dumps(
            {
                "scenarios": [
                    _scenario("Login rejected for wrong password", "negative", ["REQ-001"]),
                    _scenario("login  rejected for WRONG password", "negative", ["REQ-001"]),
                ]
            }
        )
        with pytest.raises(StageError, match="duplicate scenarios"):
            ScenarioGenerator(MockLLMClient([dup]), prompts, settings).run(analysis)

    def test_same_title_in_different_categories_is_not_a_duplicate(
        self, settings: QAOpsSettings, prompts: PromptLoader, analysis: RequirementAnalysisResult
    ) -> None:
        ok = json.dumps(
            {
                "scenarios": [
                    _scenario("Account lock behavior", "negative", ["REQ-002"]),
                    _scenario("Account lock behavior", "state_transition", ["REQ-002"]),
                ]
            }
        )
        result = ScenarioGenerator(MockLLMClient([ok]), prompts, settings).run(analysis)
        assert len(result.scenarios) == 2

    def test_zero_scenarios_is_a_stage_error(
        self, settings: QAOpsSettings, prompts: PromptLoader, analysis: RequirementAnalysisResult
    ) -> None:
        with pytest.raises(StageError, match="zero scenarios"):
            ScenarioGenerator(MockLLMClient(['{"scenarios": []}']), prompts, settings).run(analysis)

    def test_requires_prior_analysis(self, settings: QAOpsSettings, prompts: PromptLoader) -> None:
        empty = RequirementAnalysisResult(source_name="x", source_text="y")
        with pytest.raises(StageError, match="RequirementAnalyzer first"):
            ScenarioGenerator(MockLLMClient([]), prompts, settings).run(empty)

    def test_no_test_case_artifacts_in_result(self) -> None:
        # Phase 3 boundary: no test cases, priorities, test data, or expected results.
        fields = set(ScenarioDesignResult.model_fields)
        assert fields == {"analysis", "scenarios"}
        scenario_fields = set(
            __import__("qaops.models", fromlist=["Scenario"]).Scenario.model_fields
        )
        assert "priority" not in scenario_fields
        assert "test_data" not in scenario_fields
        assert "expected_result" not in scenario_fields


class TestComposedScenarioPipeline:
    @pytest.mark.parametrize(
        "example", ["login.md", "checkout.md", "video_playback.md", "fund_transfer.md"]
    )
    def test_full_run_against_each_golden_example(
        self, settings: QAOpsSettings, prompts: PromptLoader, example: str
    ) -> None:
        text = (EXAMPLES_DIR / example).read_text(encoding="utf-8")
        mock = MockLLMClient([ANALYZER_RESPONSE, RULES_RESPONSE, GAPS_RESPONSE, SCENARIOS_RESPONSE])
        pipeline = build_scenario_pipeline(mock, prompts, settings)
        assert pipeline.stage_names[-1] == "scenario_generator"

        result = pipeline.run(RequirementInput(text=text, source_name=example))
        assert isinstance(result, ScenarioDesignResult)
        assert result.analysis.source_name == example
        assert mock.call_count == 4
        # every scenario traces to a real requirement of this run
        known = {r.id for r in result.analysis.requirements}
        assert all(set(s.requirement_ids) <= known for s in result.scenarios)
        # the golden document itself reached the analyzer prompt
        assert text.splitlines()[0].lstrip("# ") in mock.requests[0].messages[0].content
