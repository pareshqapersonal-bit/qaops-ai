"""Phase 5 tests: CoverageValidator - fully deterministic, zero LLM calls.

These tests construct TestDesignResult objects directly (the validator
needs no model, so most tests need no MockLLMClient at all) and assert
exact coverage math, traceability, duplicate flagging, invalid-reference
detection, and metrics. The composed-pipeline test runs all four golden
examples through the full six-stage pipeline with a MockLLMClient
supplying the five upstream LLM responses (ADR-008, ADR-015)."""

import json
from pathlib import Path

import pytest

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.llm import MockLLMClient, PromptLoader
from qaops.models import (
    BusinessRule,
    CoverageStatus,
    Requirement,
    RequirementInput,
    Scenario,
    ScenarioCategory,
    TestCase,
    TestDesignResult,
    TestStep,
)
from qaops.pipelines.test_design import CoverageValidator, build_full_pipeline

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _tc(
    tc_id: str,
    scenario_id: str,
    req_ids: list[str],
    title: str = "a test case",
) -> TestCase:
    return TestCase(
        id=tc_id,
        scenario_id=scenario_id,
        requirement_ids=req_ids,
        title=title,
        steps=[TestStep(number=1, action="do something")],
        expected_result="something happens",
    )


def _result(
    *,
    requirements: list[Requirement],
    scenarios: list[Scenario],
    test_cases: list[TestCase],
    business_rules: list[BusinessRule] | None = None,
) -> TestDesignResult:
    return TestDesignResult(
        source_name="test",
        requirements=requirements,
        business_rules=business_rules or [],
        scenarios=scenarios,
        test_cases=test_cases,
    )


REQ1 = Requirement(id="REQ-001", title="Login", description="d")
REQ2 = Requirement(id="REQ-002", title="Lockout", description="d")
SC1 = Scenario(
    id="SC-001", title="login ok", category=ScenarioCategory.POSITIVE, requirement_ids=["REQ-001"]
)
SC2 = Scenario(
    id="SC-002",
    title="lockout",
    category=ScenarioCategory.BOUNDARY_VALUE,
    requirement_ids=["REQ-002"],
)


class TestDeterminism:
    def test_validator_constructor_takes_no_llm_client(self) -> None:
        # The zero-LLM guarantee is enforced by the signature: no client,
        # no prompts. Constructing it needs nothing.
        validator = CoverageValidator()
        assert validator.name == "coverage_validator"

    def test_repeated_runs_are_identical(self) -> None:
        data = _result(
            requirements=[REQ1, REQ2],
            scenarios=[SC1, SC2],
            test_cases=[_tc("TC-001", "SC-001", ["REQ-001"])],
        )
        first = CoverageValidator().run(data).coverage
        second = CoverageValidator().run(data).coverage
        assert first == second

    def test_input_result_is_not_mutated(self) -> None:
        data = _result(
            requirements=[REQ1],
            scenarios=[SC1],
            test_cases=[_tc("TC-001", "SC-001", ["REQ-001"])],
        )
        assert data.coverage.metrics.total_test_cases == 0
        out = CoverageValidator().run(data)
        assert data.coverage.metrics.total_test_cases == 0  # unchanged
        assert out is not data


class TestRequirementCoverage:
    def test_uncovered_requirement(self) -> None:
        data = _result(
            requirements=[REQ1, REQ2],
            scenarios=[SC1, SC2],
            test_cases=[_tc("TC-001", "SC-001", ["REQ-001"])],
        )
        report = CoverageValidator().run(data).coverage
        assert report.uncovered_requirement_ids == ["REQ-002"]
        cov = {rc.requirement_id: rc for rc in report.per_requirement}
        assert cov["REQ-001"].status is CoverageStatus.COVERED
        assert cov["REQ-001"].test_case_ids == ["TC-001"]
        assert cov["REQ-002"].status is CoverageStatus.UNCOVERED

    def test_partial_when_a_scenario_category_has_no_test_case(self) -> None:
        # REQ-001 has two scenario categories; only the positive one has a case.
        sc_neg = Scenario(
            id="SC-003",
            title="bad login",
            category=ScenarioCategory.NEGATIVE,
            requirement_ids=["REQ-001"],
        )
        data = _result(
            requirements=[REQ1],
            scenarios=[SC1, sc_neg],
            test_cases=[_tc("TC-001", "SC-001", ["REQ-001"])],
        )
        cov = CoverageValidator().run(data).coverage.per_requirement[0]
        assert cov.status is CoverageStatus.PARTIAL
        assert ScenarioCategory.NEGATIVE in cov.missing_categories


class TestBusinessRuleCoverage:
    def test_rule_covered_transitively_via_requirement(self) -> None:
        data = _result(
            requirements=[REQ2],
            business_rules=[BusinessRule(id="BR-001", requirement_id="REQ-002", rule="locks")],
            scenarios=[SC2],
            test_cases=[_tc("TC-001", "SC-002", ["REQ-002"])],
        )
        bc = CoverageValidator().run(data).coverage.per_business_rule[0]
        assert bc.status is CoverageStatus.COVERED
        assert bc.test_case_ids == ["TC-001"]

    def test_rule_uncovered_when_its_requirement_has_no_case(self) -> None:
        data = _result(
            requirements=[REQ2],
            business_rules=[BusinessRule(id="BR-001", requirement_id="REQ-002", rule="locks")],
            scenarios=[SC2],
            test_cases=[],
        )
        report = CoverageValidator().run(data).coverage
        assert report.uncovered_business_rule_ids == ["BR-001"]


class TestScenarioCoverage:
    def test_uncovered_scenario(self) -> None:
        data = _result(
            requirements=[REQ1, REQ2],
            scenarios=[SC1, SC2],
            test_cases=[_tc("TC-001", "SC-001", ["REQ-001"])],
        )
        report = CoverageValidator().run(data).coverage
        assert report.uncovered_scenario_ids == ["SC-002"]


class TestTraceability:
    def test_matrix_maps_requirements_to_cases(self) -> None:
        data = _result(
            requirements=[REQ1, REQ2],
            scenarios=[SC1, SC2],
            test_cases=[
                _tc("TC-001", "SC-001", ["REQ-001"]),
                _tc("TC-002", "SC-002", ["REQ-002"]),
            ],
        )
        entries = CoverageValidator().run(data).coverage.traceability.entries
        assert entries == {"REQ-001": ["TC-001"], "REQ-002": ["TC-002"]}


class TestDuplicateDetection:
    def test_identical_titles_flagged(self) -> None:
        data = _result(
            requirements=[REQ1],
            scenarios=[SC1],
            test_cases=[
                _tc("TC-001", "SC-001", ["REQ-001"], title="Valid login"),
                _tc("TC-002", "SC-001", ["REQ-001"], title="valid  LOGIN"),
            ],
        )
        report = CoverageValidator().run(data).coverage
        assert len(report.duplicate_pairs) == 1
        pair = report.duplicate_pairs[0]
        assert (pair.test_case_id_a, pair.test_case_id_b) == ("TC-001", "TC-002")
        assert report.suspected_duplicates == [("TC-001", "TC-002")]  # backward-compat mirror

    def test_high_title_overlap_same_scenario_flagged(self) -> None:
        data = _result(
            requirements=[REQ1],
            scenarios=[SC1],
            test_cases=[
                _tc("TC-001", "SC-001", ["REQ-001"], title="user logs in with valid password"),
                _tc("TC-002", "SC-001", ["REQ-001"], title="user logs in with valid credentials"),
            ],
        )
        report = CoverageValidator().run(data).coverage
        assert len(report.duplicate_pairs) == 1
        assert "overlap" in report.duplicate_pairs[0].reason

    def test_distinct_cases_not_flagged(self) -> None:
        data = _result(
            requirements=[REQ1],
            scenarios=[SC1],
            test_cases=[
                _tc("TC-001", "SC-001", ["REQ-001"], title="login succeeds"),
                _tc("TC-002", "SC-001", ["REQ-001"], title="password field is masked"),
            ],
        )
        report = CoverageValidator().run(data).coverage
        assert report.duplicate_pairs == []


class TestInvalidReferences:
    def test_valid_result_has_no_invalid_references(self) -> None:
        data = _result(
            requirements=[REQ1],
            scenarios=[SC1],
            test_cases=[_tc("TC-001", "SC-001", ["REQ-001"])],
        )
        report = CoverageValidator().run(data).coverage
        assert report.invalid_references == []
        assert not report.has_invalid_references

    def test_unknown_scenario_and_requirement_refs_reported(self) -> None:
        # A malformed result the upstream stages should never produce - the
        # validator reports rather than trusts.
        data = _result(
            requirements=[REQ1],
            scenarios=[SC1],
            test_cases=[_tc("TC-001", "SC-999", ["REQ-888"])],
        )
        report = CoverageValidator().run(data).coverage
        kinds = {(r.reference_kind, r.missing_id) for r in report.invalid_references}
        assert ("scenario", "SC-999") in kinds
        assert ("requirement", "REQ-888") in kinds


class TestMetrics:
    def test_percentages_and_totals(self) -> None:
        data = _result(
            requirements=[REQ1, REQ2],
            business_rules=[BusinessRule(id="BR-001", requirement_id="REQ-002", rule="r")],
            scenarios=[SC1, SC2],
            test_cases=[_tc("TC-001", "SC-001", ["REQ-001"])],
        )
        m = CoverageValidator().run(data).coverage.metrics
        assert m.total_requirements == 2
        assert m.covered_requirements == 1
        assert m.requirement_coverage_pct == 50.0
        assert m.business_rule_coverage_pct == 0.0  # rule's requirement uncovered
        assert m.scenario_coverage_pct == 50.0
        assert m.total_test_cases == 1

    def test_empty_denominators_yield_zero_not_error(self) -> None:
        data = _result(
            requirements=[REQ1],
            scenarios=[SC1],
            test_cases=[_tc("TC-001", "SC-001", ["REQ-001"])],
        )
        m = CoverageValidator().run(data).coverage.metrics
        assert m.business_rule_coverage_pct == 0.0  # no rules at all


class TestStagePreconditions:
    def test_requires_requirements(self) -> None:
        empty = TestDesignResult(source_name="x")
        with pytest.raises(StageError, match="No requirements present"):
            CoverageValidator().run(empty)


# --- Composed six-stage pipeline over the four golden examples ---------------

ANALYZER_RESPONSE = json.dumps(
    {
        "requirements": [
            {
                "title": "Primary capability",
                "description": "The main behavior.",
                "actors": ["User"],
            },
            {"title": "Secondary capability", "description": "A second behavior."},
        ]
    }
)
RULES_RESPONSE = json.dumps(
    {"rules": [{"requirement_id": "REQ-001", "rule": "a rule", "source_excerpt": ""}]}
)
GAPS_RESPONSE = json.dumps({"gaps": []})
SCENARIOS_RESPONSE = json.dumps(
    {
        "scenarios": [
            {
                "title": "Primary happy path",
                "description": "d",
                "category": "positive",
                "requirement_ids": ["REQ-001"],
            },
            {
                "title": "Secondary boundary",
                "description": "d",
                "category": "boundary_value",
                "requirement_ids": ["REQ-002"],
            },
        ]
    }
)
TEST_CASES_RESPONSE = json.dumps(
    {
        "test_cases": [
            {
                "scenario_id": "SC-001",
                "requirement_ids": ["REQ-001"],
                "title": "Primary case",
                "expected_result": "works",
                "steps": [{"action": "do it", "expected": "done"}],
                "priority": "high",
                "test_type": "functional",
            },
            {
                "scenario_id": "SC-002",
                "requirement_ids": ["REQ-002"],
                "title": "Boundary case",
                "expected_result": "handled",
                "steps": [{"action": "push limit", "expected": "rejected"}],
                "priority": "critical",
                "test_type": "boundary",
            },
        ]
    }
)


@pytest.fixture
def settings(tmp_path: Path) -> QAOpsSettings:
    return QAOpsSettings(output_dir=tmp_path / "out")


@pytest.fixture
def prompts() -> PromptLoader:
    return PromptLoader()


class TestComposedFullPipeline:
    @pytest.mark.parametrize(
        "example", ["login.md", "checkout.md", "video_playback.md", "fund_transfer.md"]
    )
    def test_all_four_golden_examples_through_validator(
        self, settings: QAOpsSettings, prompts: PromptLoader, example: str
    ) -> None:
        text = (EXAMPLES_DIR / example).read_text(encoding="utf-8")
        mock = MockLLMClient(
            [
                ANALYZER_RESPONSE,
                RULES_RESPONSE,
                GAPS_RESPONSE,
                SCENARIOS_RESPONSE,
                TEST_CASES_RESPONSE,
            ]
        )
        pipeline = build_full_pipeline(mock, prompts, settings)
        assert pipeline.stage_names[-1] == "coverage_validator"

        result = pipeline.run(RequirementInput(text=text, source_name=example))
        assert isinstance(result, TestDesignResult)
        assert mock.call_count == 5  # validator adds no LLM call

        report = result.coverage
        # Every requirement, rule, and scenario got a coverage verdict.
        assert len(report.per_requirement) == len(result.requirements)
        assert len(report.per_business_rule) == len(result.business_rules)
        assert len(report.per_scenario) == len(result.scenarios)
        # This fixture fully covers everything; no invalid references.
        assert report.invalid_references == []
        assert report.metrics.requirement_coverage_pct == 100.0
        assert report.metrics.scenario_coverage_pct == 100.0
        # Traceability closes: every mapped case is a real test case.
        real_tc_ids = {tc.id for tc in result.test_cases}
        for tc_ids in report.traceability.entries.values():
            assert set(tc_ids) <= real_tc_ids
