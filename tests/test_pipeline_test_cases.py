"""Phase 4 tests: TestCaseGenerator against MockLLMClient.

Deterministic responsibilities under test: TC-* ID assignment, code-
assigned step numbering (ADR-014), scenario/requirement reference
verification, duplicate rejection, full field mapping, the untouched
pass-through of analysis artifacts, and the composed 5-stage pipeline
across all four golden examples (ADR-008)."""

import json
from pathlib import Path

import pytest

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.llm import MockLLMClient, PromptLoader
from qaops.models import (
    ConditionDesignResult,
    Priority,
    ScenarioDesignResult,
    TestDesignResult,
)
from qaops.models import (
    RequirementInput as ReqInput,
)
from qaops.models import (
    TestType as TCType,
)
from qaops.pipelines.test_design import (
    BusinessRuleExtractor,
    RequirementAnalyzer,
    ScenarioGenerator,
    TestCaseGenerator,
    build_test_design_pipeline,
)
from qaops.pipelines.test_design.conditions import TestConditionAnalyzer

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

ANALYZER_RESPONSE = json.dumps(
    {
        "requirements": [
            {
                "title": "Login with valid credentials",
                "description": "A registered user logs in with a correct email and password.",
                "source_excerpt": "entering a correct email and password combination",
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
GAPS_RESPONSE = json.dumps(
    {
        "gaps": [
            {
                "description": "Lockout duration is not specified.",
                "severity": "blocker",
                "requirement_id": "REQ-002",
                "suggested_question": "How long does the account lock last?",
            }
        ]
    }
)
SCENARIOS_RESPONSE = json.dumps(
    {
        "scenarios": [
            {
                "title": "Successful login redirects to dashboard",
                "description": "Verifies the happy path.",
                "category": "positive",
                "requirement_ids": ["REQ-001"],
            },
            {
                "title": "Fifth consecutive failed attempt locks the account",
                "description": "Verifies the lockout boundary.",
                "category": "boundary_value",
                "requirement_ids": ["REQ-002"],
            },
        ]
    }
)


CONDITIONS_RESPONSE = json.dumps(
    {
        "conditions": [
            {
                "scenario_id": "SC-001",
                "requirement_ids": ["REQ-001"],
                "business_rule_ids": [],
                "category": "positive",
                "description": "Valid registered credentials are accepted.",
                "rationale": "REQ-001 states valid login succeeds.",
                "source_basis": "explicit_requirement",
                "status": "resolved",
                "parameters": {},
                "gap_reference": "",
            },
            {
                "scenario_id": "SC-002",
                "requirement_ids": ["REQ-002"],
                "business_rule_ids": [],
                "category": "boundary",
                "description": "Account locks exactly on the fifth failed attempt.",
                "rationale": "BR sets lockout at 5 attempts.",
                "source_basis": "derived_boundary",
                "status": "resolved",
                "parameters": {"attempts": "5"},
                "gap_reference": "",
            },
        ]
    }
)


def _test_case(
    title: str,
    scenario_id: str = "SC-001",
    condition_id: str = "COND-001",
    req_ids: list[str] | None = None,
    steps: list[dict[str, str]] | None = None,
    **overrides: object,
) -> dict[str, object]:
    base: dict[str, object] = {
        "scenario_id": scenario_id,
        "condition_id": condition_id,
        "requirement_ids": req_ids or ["REQ-001"],
        "module": "Authentication",
        "feature": "Login",
        "title": title,
        "objective": f"Prove: {title}.",
        "preconditions": ["A registered user exists"],
        "test_data": {"email": "user@example.com", "password": "Valid@123"},
        "steps": steps
        or [
            {"action": "Open the login page.", "expected": "Login form is displayed."},
            {"action": "Enter the email from test_data.", "expected": ""},
            {"action": "Enter the password and click Log in.", "expected": ""},
        ],
        "expected_result": "The user lands on the dashboard.",
        "priority": "high",
        "test_type": "functional",
        "tags": ["smoke", "login"],
    }
    base.update(overrides)
    return base


TEST_CASES_RESPONSE = json.dumps(
    {
        "test_cases": [
            _test_case("Login succeeds with valid registered credentials"),
            _test_case(
                "Account locks on the fifth consecutive failed attempt",
                scenario_id="SC-002",
                condition_id="COND-002",
                req_ids=["REQ-002"],
                priority="critical",
                test_type="boundary",
                steps=[
                    {"action": "Attempt login with a wrong password four times.", "expected": ""},
                    {
                        "action": "Attempt login with a wrong password a fifth time.",
                        "expected": "The lockout message is shown.",
                    },
                ],
            ),
            _test_case(
                "Locked account rejects correct credentials",
                scenario_id="SC-002",
                condition_id="COND-002",
                req_ids=["REQ-002"],
                test_type="negative",
                expected_result="The locked account is refused despite correct credentials.",
            ),
        ]
    }
)


@pytest.fixture
def settings(tmp_path: Path) -> QAOpsSettings:
    return QAOpsSettings(output_dir=tmp_path / "out")


@pytest.fixture
def prompts() -> PromptLoader:
    return PromptLoader()


def login_input() -> ReqInput:
    return ReqInput(
        text=(EXAMPLES_DIR / "login.md").read_text(encoding="utf-8"), source_name="login.md"
    )


@pytest.fixture
def designed(settings: QAOpsSettings, prompts: PromptLoader) -> ScenarioDesignResult:
    analysis = RequirementAnalyzer(MockLLMClient([ANALYZER_RESPONSE]), prompts, settings).run(
        login_input()
    )
    enriched = BusinessRuleExtractor(MockLLMClient([RULES_RESPONSE]), prompts, settings).run(
        analysis
    )
    return ScenarioGenerator(MockLLMClient([SCENARIOS_RESPONSE]), prompts, settings).run(enriched)


@pytest.fixture
def conditioned(
    designed: ScenarioDesignResult, settings: QAOpsSettings, prompts: PromptLoader
) -> ConditionDesignResult:
    return TestConditionAnalyzer(MockLLMClient([CONDITIONS_RESPONSE]), prompts, settings).run(
        designed
    )


class TestTestCaseGenerator:
    def test_assigns_tc_ids_and_maps_all_fields(
        self, settings: QAOpsSettings, prompts: PromptLoader, conditioned: ConditionDesignResult
    ) -> None:
        mock = MockLLMClient([TEST_CASES_RESPONSE])
        result = TestCaseGenerator(mock, prompts, settings).run(conditioned)

        assert isinstance(result, TestDesignResult)
        assert [t.id for t in result.test_cases] == ["TC-001", "TC-002", "TC-003"]

        first = result.test_cases[0]
        assert first.scenario_id == "SC-001"
        assert first.module == "Authentication"
        assert first.test_data["email"] == "user@example.com"
        assert first.priority is Priority.HIGH
        assert first.test_type is TCType.FUNCTIONAL
        assert first.tags == ["smoke", "login"]

        second = result.test_cases[1]
        assert second.priority is Priority.CRITICAL
        assert second.test_type is TCType.BOUNDARY

    def test_step_numbers_assigned_by_code_from_order(
        self, settings: QAOpsSettings, prompts: PromptLoader, conditioned: ConditionDesignResult
    ) -> None:
        mock = MockLLMClient([TEST_CASES_RESPONSE])
        result = TestCaseGenerator(mock, prompts, settings).run(conditioned)
        for tc in result.test_cases:
            assert [s.number for s in tc.steps] == list(range(1, len(tc.steps) + 1))
        assert result.test_cases[1].steps[1].expected == "The lockout message is shown."

    def test_analysis_artifacts_pass_through_untouched(
        self, settings: QAOpsSettings, prompts: PromptLoader, conditioned: ConditionDesignResult
    ) -> None:
        mock = MockLLMClient([TEST_CASES_RESPONSE])
        result = TestCaseGenerator(mock, prompts, settings).run(conditioned)
        assert result.requirements == conditioned.scenario_design.analysis.requirements
        assert result.business_rules == conditioned.scenario_design.analysis.business_rules
        assert result.gap_report == conditioned.scenario_design.analysis.gap_report
        assert result.scenarios == conditioned.scenario_design.scenarios
        assert result.source_name == "login.md"

    def test_coverage_is_left_for_phase_5(
        self, settings: QAOpsSettings, prompts: PromptLoader, conditioned: ConditionDesignResult
    ) -> None:
        mock = MockLLMClient([TEST_CASES_RESPONSE])
        result = TestCaseGenerator(mock, prompts, settings).run(conditioned)
        assert result.coverage.per_requirement == []
        assert result.coverage.traceability.entries == {}
        assert result.coverage.suspected_duplicates == []

    def test_prompt_contains_scenarios_requirements_rules(
        self, settings: QAOpsSettings, prompts: PromptLoader, conditioned: ConditionDesignResult
    ) -> None:
        mock = MockLLMClient([TEST_CASES_RESPONSE])
        TestCaseGenerator(mock, prompts, settings).run(conditioned)
        content = mock.requests[0].messages[0].content
        assert "SC-001" in content and "SC-002" in content
        assert "REQ-001" in content
        # business rules must reach the prompt (rules_json placeholder)
        assert "5 consecutive failed login attempts" in content
        assert "Do NOT include step numbers" in content
        assert "never invent IDs" in content

    def test_unknown_scenario_reference_is_a_stage_error(
        self, settings: QAOpsSettings, prompts: PromptLoader, conditioned: ConditionDesignResult
    ) -> None:
        bad = json.dumps({"test_cases": [_test_case("x", scenario_id="SC-099")]})
        with pytest.raises(StageError, match="SC-099"):
            TestCaseGenerator(MockLLMClient([bad]), prompts, settings).run(conditioned)

    def test_unknown_condition_reference_is_a_stage_error(
        self, settings: QAOpsSettings, prompts: PromptLoader, conditioned: ConditionDesignResult
    ) -> None:
        bad = json.dumps({"test_cases": [_test_case("x", condition_id="COND-099")]})
        with pytest.raises(StageError, match="COND-099"):
            TestCaseGenerator(MockLLMClient([bad]), prompts, settings).run(conditioned)

    def test_unknown_requirement_reference_is_a_stage_error(
        self, settings: QAOpsSettings, prompts: PromptLoader, conditioned: ConditionDesignResult
    ) -> None:
        bad = json.dumps({"test_cases": [_test_case("x", req_ids=["REQ-777"])]})
        with pytest.raises(StageError, match="REQ-777"):
            TestCaseGenerator(MockLLMClient([bad]), prompts, settings).run(conditioned)

    def test_case_scenario_must_match_its_condition_scenario(
        self, settings: QAOpsSettings, prompts: PromptLoader, conditioned: ConditionDesignResult
    ) -> None:
        # COND-001 belongs to SC-001; claiming SC-002 for it is a hallucinated
        # cross-link the per-condition check (ADR-014/036) must reject.
        bad = json.dumps(
            {
                "test_cases": [
                    _test_case("cross-linked", scenario_id="SC-002", condition_id="COND-001")
                ]
            }
        )
        with pytest.raises(StageError, match="belongs to scenario"):
            TestCaseGenerator(MockLLMClient([bad]), prompts, settings).run(conditioned)

    def test_requirement_not_linked_to_its_condition_is_a_stage_error(
        self, settings: QAOpsSettings, prompts: PromptLoader, conditioned: ConditionDesignResult
    ) -> None:
        # COND-002 is linked only to REQ-002; REQ-001 is a hallucinated cross-link.
        bad = json.dumps(
            {
                "test_cases": [
                    _test_case(
                        "cross-linked",
                        scenario_id="SC-002",
                        condition_id="COND-002",
                        req_ids=["REQ-001"],
                    )
                ]
            }
        )
        with pytest.raises(StageError, match="not linked to that condition"):
            TestCaseGenerator(MockLLMClient([bad]), prompts, settings).run(conditioned)

    def test_exact_duplicate_cases_are_dropped_not_raised(
        self, settings: QAOpsSettings, prompts: PromptLoader, conditioned: ConditionDesignResult
    ) -> None:
        # Two cases with the same condition, data, and expected result collapse
        # to one via the canonical signature (ADR-036) - dropped, not an error.
        dup = json.dumps(
            {
                "test_cases": [
                    _test_case("Login succeeds with valid credentials"),
                    _test_case("login  succeeds WITH valid credentials"),
                ]
            }
        )
        result = TestCaseGenerator(MockLLMClient([dup]), prompts, settings).run(conditioned)
        assert len(result.test_cases) == 1

    def test_boundary_variants_survive_deduplication(
        self, settings: QAOpsSettings, prompts: PromptLoader, conditioned: ConditionDesignResult
    ) -> None:
        # Same condition, different data values -> different signatures -> both kept.
        variants = json.dumps(
            {
                "test_cases": [
                    _test_case(
                        "Attempts below threshold",
                        scenario_id="SC-002",
                        condition_id="COND-002",
                        req_ids=["REQ-002"],
                        test_data={"attempts": "4"},
                        expected_result="Account remains unlocked at four attempts.",
                    ),
                    _test_case(
                        "Attempts at threshold",
                        scenario_id="SC-002",
                        condition_id="COND-002",
                        req_ids=["REQ-002"],
                        test_data={"attempts": "5"},
                        expected_result="Account locks at five attempts.",
                    ),
                ]
            }
        )
        result = TestCaseGenerator(MockLLMClient([variants]), prompts, settings).run(conditioned)
        assert len(result.test_cases) == 2

    def test_missing_mandatory_fields_trigger_repair_retry(
        self, settings: QAOpsSettings, prompts: PromptLoader, conditioned: ConditionDesignResult
    ) -> None:
        # empty title and empty steps violate the wire schema
        bad = json.dumps({"test_cases": [_test_case("", steps=[])]})
        mock = MockLLMClient([bad, TEST_CASES_RESPONSE])
        result = TestCaseGenerator(mock, prompts, settings).run(conditioned)
        assert mock.call_count == 2
        assert len(result.test_cases) == 3

    def test_invalid_priority_triggers_repair_retry(
        self, settings: QAOpsSettings, prompts: PromptLoader, conditioned: ConditionDesignResult
    ) -> None:
        bad = json.dumps({"test_cases": [_test_case("x", priority="urgent")]})
        mock = MockLLMClient([bad, TEST_CASES_RESPONSE])
        TestCaseGenerator(mock, prompts, settings).run(conditioned)
        assert mock.call_count == 2

    def test_zero_test_cases_is_a_stage_error(
        self, settings: QAOpsSettings, prompts: PromptLoader, conditioned: ConditionDesignResult
    ) -> None:
        with pytest.raises(StageError, match="zero test cases"):
            TestCaseGenerator(MockLLMClient(['{"test_cases": []}']), prompts, settings).run(
                conditioned
            )

    def test_requires_prior_conditions(
        self, settings: QAOpsSettings, prompts: PromptLoader, conditioned: ConditionDesignResult
    ) -> None:
        empty = ConditionDesignResult(scenario_design=conditioned.scenario_design, conditions=[])
        with pytest.raises(StageError, match="TestConditionAnalyzer first"):
            TestCaseGenerator(MockLLMClient([]), prompts, settings).run(empty)


class TestComposedTestDesignPipeline:
    @pytest.mark.parametrize(
        "example", ["login.md", "checkout.md", "video_playback.md", "fund_transfer.md"]
    )
    def test_full_five_stage_run_per_golden_example(
        self, settings: QAOpsSettings, prompts: PromptLoader, example: str
    ) -> None:
        text = (EXAMPLES_DIR / example).read_text(encoding="utf-8")
        mock = MockLLMClient(
            [
                ANALYZER_RESPONSE,
                RULES_RESPONSE,
                GAPS_RESPONSE,
                SCENARIOS_RESPONSE,
                CONDITIONS_RESPONSE,
                TEST_CASES_RESPONSE,
            ]
        )
        pipeline = build_test_design_pipeline(mock, prompts, settings)
        assert pipeline.stage_names == [
            "requirement_analyzer",
            "business_rule_extractor",
            "gap_analyzer",
            "scenario_generator",
            "test_condition_analyzer",
            "test_case_generator",
        ]
        result = pipeline.run(ReqInput(text=text, source_name=example))
        assert isinstance(result, TestDesignResult)
        assert result.source_name == example
        assert mock.call_count == 6

        # full traceability closure: every TC -> a real SC and real REQs of this run
        known_scenarios = {s.id for s in result.scenarios}
        known_requirements = {r.id for r in result.requirements}
        known_conditions = {c.id for c in result.conditions}
        for tc in result.test_cases:
            assert tc.scenario_id in known_scenarios
            assert set(tc.requirement_ids) <= known_requirements
            assert tc.condition_id in known_conditions
            assert [s.number for s in tc.steps] == list(range(1, len(tc.steps) + 1))
        assert result.gap_report.has_blockers  # gap report survived to the end
