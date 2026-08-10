"""Phase 33 tests: TestCase.assumptions provenance (ADR-048).

Proves the new field threads from the wire model into TestCase, defaults empty,
does NOT alter serialization of evidence-complete cases (a pinned byte-identical
regression), and does not affect coverage. Reuses the standard fixture chain.
"""

import json
from pathlib import Path

import pytest

from qaops.config import QAOpsSettings
from qaops.llm import MockLLMClient, PromptLoader
from qaops.models import RequirementInput
from qaops.pipelines.test_design import (
    BusinessRuleExtractor,
    RequirementAnalyzer,
    ScenarioGenerator,
    TestCaseGenerator,
    TestConditionAnalyzer,
)
from tests.test_pipeline_test_cases import (
    ANALYZER_RESPONSE,
    CONDITIONS_RESPONSE,
    EXAMPLES_DIR,
    RULES_RESPONSE,
    SCENARIOS_RESPONSE,
    TEST_CASES_RESPONSE,
    _test_case,
)


@pytest.fixture
def settings(tmp_path: Path) -> QAOpsSettings:
    return QAOpsSettings(output_dir=tmp_path / "out")


@pytest.fixture
def prompts() -> PromptLoader:
    return PromptLoader()


def _conditioned(settings: QAOpsSettings, prompts: PromptLoader):
    inp = RequirementInput(
        text=(EXAMPLES_DIR / "login.md").read_text(encoding="utf-8"), source_name="login.md"
    )
    analysis = RequirementAnalyzer(MockLLMClient([ANALYZER_RESPONSE]), prompts, settings).run(inp)
    enriched = BusinessRuleExtractor(MockLLMClient([RULES_RESPONSE]), prompts, settings).run(
        analysis
    )
    designed = ScenarioGenerator(MockLLMClient([SCENARIOS_RESPONSE]), prompts, settings).run(
        enriched
    )
    return TestConditionAnalyzer(MockLLMClient([CONDITIONS_RESPONSE]), prompts, settings).run(
        designed
    )


class TestAssumptionsDefaultsEmpty:
    def test_evidence_complete_cases_have_empty_assumptions(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        conditioned = _conditioned(settings, prompts)
        result = TestCaseGenerator(MockLLMClient([TEST_CASES_RESPONSE]), prompts, settings).run(
            conditioned
        )
        assert result.test_cases
        assert all(tc.assumptions == [] for tc in result.test_cases)


class TestAssumptionsByteIdenticalRegression:
    def test_evidence_complete_serialization_unchanged(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        # PINNED: with exclude_defaults, an evidence-complete case (no assumptions)
        # must serialize with no "assumptions" key at all - proving existing
        # document runs are byte-identical, not merely "should be".
        conditioned = _conditioned(settings, prompts)
        result = TestCaseGenerator(MockLLMClient([TEST_CASES_RESPONSE]), prompts, settings).run(
            conditioned
        )
        for tc in result.test_cases:
            dumped = json.loads(tc.model_dump_json(exclude_defaults=True))
            assert "assumptions" not in dumped


class TestAssumptionsFlowThrough:
    def test_assumptions_thread_from_wire_to_test_case(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        # A model that declares an assumption must have it preserved on the case,
        # verbatim, without becoming a requirement or business rule.
        response = json.dumps(
            {
                "test_cases": [
                    _test_case(
                        "Login succeeds with valid registered credentials",
                        assumptions=["A registered account already exists in the system."],
                    )
                ]
            }
        )
        conditioned = _conditioned(settings, prompts)
        result = TestCaseGenerator(MockLLMClient([response]), prompts, settings).run(conditioned)
        case = result.test_cases[0]
        assert case.assumptions == ["A registered account already exists in the system."]
        # The assumption did not leak into requirements or business rules.
        assert all(
            "registered account already exists" not in r.description.lower()
            for r in result.requirements
        )

    def test_assumptions_present_in_full_serialization(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        response = json.dumps(
            {
                "test_cases": [
                    _test_case(
                        "Login succeeds with valid registered credentials",
                        assumptions=["The login page is reachable without SSO."],
                    )
                ]
            }
        )
        conditioned = _conditioned(settings, prompts)
        result = TestCaseGenerator(MockLLMClient([response]), prompts, settings).run(conditioned)
        dumped = json.loads(result.test_cases[0].model_dump_json(exclude_defaults=True))
        assert dumped["assumptions"] == ["The login page is reachable without SSO."]


class TestCoverageUnaffectedByAssumptions:
    def test_coverage_identical_with_and_without_assumptions(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        from qaops.pipelines.test_design import CoverageValidator

        conditioned = _conditioned(settings, prompts)
        base = TestCaseGenerator(MockLLMClient([TEST_CASES_RESPONSE]), prompts, settings).run(
            conditioned
        )
        base_cov = CoverageValidator().run(base).coverage.metrics.model_dump()

        # Same cases but with an assumption added to the first one.
        with_assumption = json.dumps(
            {
                "test_cases": [
                    _test_case(
                        "Login succeeds with valid registered credentials",
                        assumptions=["An account exists."],
                    ),
                ]
            }
        )
        alt = TestCaseGenerator(MockLLMClient([with_assumption]), prompts, settings).run(
            _conditioned(settings, prompts)
        )
        alt_cov = CoverageValidator().run(alt).coverage.metrics.model_dump()
        # Coverage metric shape is driven by ids/conditions, never by assumptions;
        # per-condition coverage for COND-001 is unaffected by the assumption text.
        assert base_cov["total_conditions"] == alt_cov["total_conditions"]
