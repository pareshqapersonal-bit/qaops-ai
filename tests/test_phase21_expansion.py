"""Phase 21 exhaustive test-design regression suite (ADR-036).

Proves the condition-driven expansion behaviour end-to-end with MockLLMClient
(no live calls): one scenario -> many conditions, one condition -> many cases,
no fixed ratio, technique-based derivation, evidence binding, ambiguity->gap,
dedup with boundary survival, traceability, coverage arithmetic, truncation
semantics, and both entry points. Uses a BOGO-style fixture as ONE example; no
BOGO logic lives in production code.
"""

import json

import pytest

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.entrypoints.builder import build_pipeline_for
from qaops.entrypoints.entry_point import EntryPoint
from qaops.llm import MockLLMClient, PromptLoader
from qaops.models import (
    RequirementInput,
    ScenarioDesignResult,
    TestDesignResult,
)
from qaops.models.enums import ConditionStatus
from qaops.pipelines.test_design import (
    BusinessRuleExtractor,
    RequirementAnalyzer,
    ScenarioGenerator,
    TestCaseGenerator,
    build_test_design_pipeline,
)
from qaops.pipelines.test_design.conditions import TestConditionAnalyzer
from qaops.pipelines.test_design.coverage import CoverageValidator


@pytest.fixture
def settings(tmp_path):  # type: ignore[no-untyped-def]
    return QAOpsSettings(output_dir=tmp_path / "out")


@pytest.fixture
def prompts() -> PromptLoader:
    return PromptLoader()


# --- BOGO-style analysis fixture (one example; not product logic) ------------

ANALYZER = json.dumps(
    {
        "requirements": [
            {"title": "BOGO eligibility", "description": "Buy-one-get-one on eligible items."}
        ]
    }
)
RULES = json.dumps(
    {
        "rules": [
            {
                "requirement_id": "REQ-001",
                "rule": "BOGO applies when at least two eligible units are present.",
                "source_excerpt": "at least two eligible units",
            }
        ]
    }
)
GAPS = json.dumps({"gaps": []})
SCENARIOS = json.dumps(
    {
        "scenarios": [
            {
                "title": "Apply BOGO to eligible cart",
                "description": "Cart with eligible items receives the offer.",
                "category": "positive",
                "requirement_ids": ["REQ-001"],
            }
        ]
    }
)


def _make_scenario_design() -> ScenarioDesignResult:
    # Build via the real stages so IDs are assigned deterministically.
    settings = QAOpsSettings()
    prompts = PromptLoader()
    a = RequirementAnalyzer(MockLLMClient([ANALYZER]), prompts, settings).run(
        RequirementInput(text="Buy one get one on eligible items.", source_name="bogo.md")
    )
    enriched = BusinessRuleExtractor(MockLLMClient([RULES]), prompts, settings).run(a)
    return ScenarioGenerator(MockLLMClient([SCENARIOS]), prompts, settings).run(enriched)


# Multiple materially distinct conditions from ONE scenario, driven by the
# documented quantity threshold (boundary + equivalence + negative + positive).
CONDITIONS_MANY = json.dumps(
    {
        "conditions": [
            {
                "scenario_id": "SC-001",
                "requirement_ids": ["REQ-001"],
                "business_rule_ids": ["BR-001"],
                "category": "boundary",
                "description": "Quantity exactly at the threshold of two.",
                "rationale": "BR-001 sets threshold at >= 2.",
                "source_basis": "derived_boundary",
                "status": "resolved",
                "parameters": {"quantity": "2"},
                "gap_reference": "",
            },
            {
                "scenario_id": "SC-001",
                "requirement_ids": ["REQ-001"],
                "business_rule_ids": ["BR-001"],
                "category": "boundary",
                "description": "Quantity just below the threshold.",
                "rationale": "BR-001 threshold boundary.",
                "source_basis": "derived_boundary",
                "status": "resolved",
                "parameters": {"quantity": "1"},
                "gap_reference": "",
            },
            {
                "scenario_id": "SC-001",
                "requirement_ids": ["REQ-001"],
                "business_rule_ids": ["BR-001"],
                "category": "positive",
                "description": "Quantity above threshold applies the offer.",
                "rationale": "BR-001 satisfied.",
                "source_basis": "derived_boundary",
                "status": "resolved",
                "parameters": {"quantity": "3"},
                "gap_reference": "",
            },
            {
                "scenario_id": "SC-001",
                "requirement_ids": ["REQ-001"],
                "business_rule_ids": [],
                "category": "eligibility",
                "description": "Ineligible SKU does not receive the offer.",
                "rationale": "Only eligible items qualify per REQ-001.",
                "source_basis": "explicit_requirement",
                "status": "resolved",
                "parameters": {"eligibility": "ineligible"},
                "gap_reference": "",
            },
        ]
    }
)


def _condition_analyzer(settings, prompts, response):  # type: ignore[no-untyped-def]
    return TestConditionAnalyzer(MockLLMClient([response]), prompts, settings)


class TestScenarioProducesManyConditions:
    def test_one_scenario_yields_multiple_conditions(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        design = _make_scenario_design()
        result = _condition_analyzer(settings, prompts, CONDITIONS_MANY).run(design)
        assert len(result.conditions) == 4
        # All from the single scenario, with deterministic COND-* IDs.
        assert {c.scenario_id for c in result.conditions} == {"SC-001"}
        assert [c.id for c in result.conditions] == ["COND-001", "COND-002", "COND-003", "COND-004"]

    def test_boundary_and_negative_and_eligibility_present(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        design = _make_scenario_design()
        result = _condition_analyzer(settings, prompts, CONDITIONS_MANY).run(design)
        categories = {c.category.value for c in result.conditions}
        assert "boundary" in categories
        assert "eligibility" in categories
        # boundary variants have distinct quantity parameters
        quantities = {
            c.parameters.get("quantity") for c in result.conditions if "quantity" in c.parameters
        }
        assert quantities == {"1", "2", "3"}


class TestEvidenceBinding:
    def test_derived_condition_without_evidence_is_rejected(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        design = _make_scenario_design()
        bad = json.dumps(
            {
                "conditions": [
                    {
                        "scenario_id": "SC-001",
                        "requirement_ids": [],
                        "business_rule_ids": [],
                        "category": "boundary",
                        "description": "A boundary with no cited rule.",
                        "rationale": "",
                        "source_basis": "derived_boundary",
                        "status": "resolved",
                        "parameters": {"quantity": "2"},
                        "gap_reference": "",
                    }
                ]
            }
        )
        with pytest.raises(StageError, match="cites no requirement or business rule"):
            _condition_analyzer(settings, prompts, bad).run(design)

    def test_unknown_scenario_reference_is_rejected(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        design = _make_scenario_design()
        bad = json.dumps(
            {
                "conditions": [
                    {
                        "scenario_id": "SC-099",
                        "requirement_ids": ["REQ-001"],
                        "business_rule_ids": [],
                        "category": "positive",
                        "description": "x",
                        "rationale": "",
                        "source_basis": "explicit_requirement",
                        "status": "resolved",
                        "parameters": {},
                        "gap_reference": "",
                    }
                ]
            }
        )
        with pytest.raises(StageError, match="SC-099"):
            _condition_analyzer(settings, prompts, bad).run(design)


class TestAmbiguityBecomesGap:
    def test_unresolved_condition_creates_gap_and_is_preserved(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        design = _make_scenario_design()
        unresolved = json.dumps(
            {
                "conditions": [
                    {
                        "scenario_id": "SC-001",
                        "requirement_ids": ["REQ-001"],
                        "business_rule_ids": [],
                        "category": "state_transition",
                        "description": "Eligible item removed after the offer is applied.",
                        "rationale": "Scenario implies cart mutation.",
                        "source_basis": "scenario",
                        "status": "unresolved",
                        "parameters": {},
                        "gap_reference": (
                            "Behaviour when an eligible item is removed after "
                            "BOGO is applied is not specified."
                        ),
                    }
                ]
            }
        )
        result = _condition_analyzer(settings, prompts, unresolved).run(design)
        # Condition preserved and marked unresolved.
        assert len(result.conditions) == 1
        assert result.conditions[0].status is ConditionStatus.UNRESOLVED
        # A gap was synthesized and linked.
        gaps = result.scenario_design.analysis.gap_report.gaps
        assert any("removed after" in g.description for g in gaps)

    def test_duplicate_gap_is_not_added_twice(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        design = _make_scenario_design()
        # Two unresolved conditions with the same gap text -> one gap.
        text = "Behaviour on removal is unspecified."
        payload = json.dumps(
            {
                "conditions": [
                    {
                        "scenario_id": "SC-001",
                        "requirement_ids": ["REQ-001"],
                        "business_rule_ids": [],
                        "category": "state_transition",
                        "description": "removal case A",
                        "rationale": "",
                        "source_basis": "scenario",
                        "status": "unresolved",
                        "parameters": {},
                        "gap_reference": text,
                    },
                    {
                        "scenario_id": "SC-001",
                        "requirement_ids": ["REQ-001"],
                        "business_rule_ids": [],
                        "category": "state_transition",
                        "description": "removal case B",
                        "rationale": "",
                        "source_basis": "scenario",
                        "status": "unresolved",
                        "parameters": {"variant": "b"},
                        "gap_reference": text,
                    },
                ]
            }
        )
        result = _condition_analyzer(settings, prompts, payload).run(design)
        matching = [
            g
            for g in result.scenario_design.analysis.gap_report.gaps
            if "removal" in g.description.lower()
        ]
        assert len(matching) == 1


class TestConditionDeduplication:
    def test_exact_duplicate_conditions_collapse(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        design = _make_scenario_design()
        dup = json.dumps(
            {
                "conditions": [
                    {
                        "scenario_id": "SC-001",
                        "requirement_ids": ["REQ-001"],
                        "business_rule_ids": ["BR-001"],
                        "category": "boundary",
                        "description": "Quantity at threshold.",
                        "rationale": "",
                        "source_basis": "derived_boundary",
                        "status": "resolved",
                        "parameters": {"quantity": "2"},
                        "gap_reference": "",
                    },
                    {
                        "scenario_id": "SC-001",
                        "requirement_ids": ["REQ-001"],
                        "business_rule_ids": ["BR-001"],
                        "category": "boundary",
                        "description": "Quantity AT threshold restated.",
                        "rationale": "",
                        "source_basis": "derived_boundary",
                        "status": "resolved",
                        "parameters": {"quantity": "2"},
                        "gap_reference": "",
                    },
                ]
            }
        )
        result = _condition_analyzer(settings, prompts, dup).run(design)
        assert len(result.conditions) == 1

    def test_boundary_variants_survive(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        design = _make_scenario_design()
        result = _condition_analyzer(settings, prompts, CONDITIONS_MANY).run(design)
        # quantity 1, 2, 3 all distinct -> all survive
        qtys = sorted(
            c.parameters["quantity"] for c in result.conditions if "quantity" in c.parameters
        )
        assert qtys == ["1", "2", "3"]


class TestExpansionBounds:
    def test_condition_bound_truncates_and_flags(self, tmp_path, prompts) -> None:  # type: ignore[no-untyped-def]
        settings = QAOpsSettings(output_dir=tmp_path / "o", max_conditions_per_scenario=2)
        design = _make_scenario_design()
        result = _condition_analyzer(settings, prompts, CONDITIONS_MANY).run(design)
        assert len(result.conditions) == 2  # capped
        assert result.expansion_truncated is True
        assert "limit reached" in result.truncation_note.lower()


class TestFullExpansionAndCoverage:
    def _cases_for(self, conditions) -> str:  # type: ignore[no-untyped-def]
        # One case per condition, plus an extra materially-distinct case for the
        # first condition (proving 1 condition -> multiple cases).
        cases = []
        for c in conditions:
            cases.append(
                {
                    "scenario_id": c.scenario_id,
                    "condition_id": c.id,
                    "requirement_ids": c.requirement_ids or ["REQ-001"],
                    "title": f"Case for {c.id}",
                    "expected_result": "documented outcome",
                    "steps": [{"action": "do", "expected": "ok"}],
                    "priority": "high",
                    "test_type": "functional",
                    "test_data": dict(c.parameters),
                }
            )
        first = conditions[0]
        cases.append(
            {
                "scenario_id": first.scenario_id,
                "condition_id": first.id,
                "requirement_ids": first.requirement_ids or ["REQ-001"],
                "title": f"Second case for {first.id}",
                "expected_result": "a different documented outcome",
                "steps": [{"action": "do differently", "expected": "ok"}],
                "priority": "medium",
                "test_type": "boundary",
                "test_data": {**first.parameters, "variant": "second"},
            }
        )
        return json.dumps({"test_cases": cases})

    def test_condition_yields_multiple_cases_and_no_fixed_ratio(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        design = _make_scenario_design()
        cond_result = _condition_analyzer(settings, prompts, CONDITIONS_MANY).run(design)
        cases_json = self._cases_for(cond_result.conditions)
        final = TestCaseGenerator(MockLLMClient([cases_json]), prompts, settings).run(cond_result)
        # 4 conditions, 5 cases -> not 1:1 with scenarios (1 scenario) nor with conditions.
        assert len(final.scenarios) == 1
        assert len(final.conditions) == 4
        assert len(final.test_cases) == 5
        # COND-001 has two cases.
        first_cases = [tc for tc in final.test_cases if tc.condition_id == "COND-001"]
        assert len(first_cases) == 2

    def test_coverage_dimensions_and_traceability(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        design = _make_scenario_design()
        cond_result = _condition_analyzer(settings, prompts, CONDITIONS_MANY).run(design)
        cases_json = self._cases_for(cond_result.conditions)
        final = TestCaseGenerator(MockLLMClient([cases_json]), prompts, settings).run(cond_result)
        covered = CoverageValidator().run(final)
        m = covered.coverage.metrics
        assert m.total_conditions == 4
        assert m.covered_conditions == 4  # each resolved condition has a real case
        assert m.condition_coverage_pct == 100.0
        assert m.total_scenarios == 1
        # Every TC traces to a real condition and scenario.
        cond_ids = {c.id for c in final.conditions}
        for tc in covered.test_cases:
            assert tc.condition_id in cond_ids
        # per-condition coverage present
        assert len(covered.coverage.per_condition) == 4

    def test_unresolved_condition_reduces_condition_coverage(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        design = _make_scenario_design()
        payload = json.dumps(
            {
                "conditions": [
                    {
                        "scenario_id": "SC-001",
                        "requirement_ids": ["REQ-001"],
                        "business_rule_ids": ["BR-001"],
                        "category": "boundary",
                        "description": "Quantity at threshold.",
                        "rationale": "",
                        "source_basis": "derived_boundary",
                        "status": "resolved",
                        "parameters": {"quantity": "2"},
                        "gap_reference": "",
                    },
                    {
                        "scenario_id": "SC-001",
                        "requirement_ids": ["REQ-001"],
                        "business_rule_ids": [],
                        "category": "state_transition",
                        "description": "Removal after apply.",
                        "rationale": "",
                        "source_basis": "scenario",
                        "status": "unresolved",
                        "parameters": {},
                        "gap_reference": "Removal behaviour unspecified.",
                    },
                ]
            }
        )
        cond_result = _condition_analyzer(settings, prompts, payload).run(design)
        # One case for the resolved condition only.
        cases = json.dumps(
            {
                "test_cases": [
                    {
                        "scenario_id": "SC-001",
                        "condition_id": "COND-001",
                        "requirement_ids": ["REQ-001"],
                        "title": "resolved case",
                        "expected_result": "offer applies",
                        "steps": [{"action": "do", "expected": "ok"}],
                        "priority": "high",
                        "test_type": "boundary",
                    }
                ]
            }
        )
        final = TestCaseGenerator(MockLLMClient([cases]), prompts, settings).run(cond_result)
        covered = CoverageValidator().run(final)
        m = covered.coverage.metrics
        assert m.total_conditions == 2
        assert m.covered_conditions == 1  # unresolved one is NOT covered
        assert m.unresolved_conditions == 1
        assert m.condition_coverage_pct == 50.0

    def test_truncation_prevents_exhaustive_claim(self, tmp_path, prompts) -> None:  # type: ignore[no-untyped-def]
        settings = QAOpsSettings(output_dir=tmp_path / "o", max_conditions_per_scenario=2)
        design = _make_scenario_design()
        cond_result = _condition_analyzer(settings, prompts, CONDITIONS_MANY).run(design)
        cases_json = self._cases_for(cond_result.conditions)
        final = TestCaseGenerator(MockLLMClient([cases_json]), prompts, settings).run(cond_result)
        covered = CoverageValidator().run(final)
        assert covered.expansion_truncated is True
        assert covered.coverage.metrics.expansion_truncated is True


class TestEntryPoints:
    def test_scenario_file_entry_point_runs_condition_then_cases(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        design = _make_scenario_design()
        cases_json = json.dumps(
            {
                "test_cases": [
                    {
                        "scenario_id": "SC-001",
                        "condition_id": "COND-001",
                        "requirement_ids": ["REQ-001"],
                        "title": "case",
                        "expected_result": "ok",
                        "steps": [{"action": "do", "expected": "ok"}],
                        "priority": "high",
                        "test_type": "functional",
                    }
                ]
            }
        )
        client = MockLLMClient([CONDITIONS_MANY, cases_json])
        pipeline = build_pipeline_for(EntryPoint.SCENARIOS, client, prompts, settings)
        assert pipeline.stage_names == [
            "test_condition_analyzer",
            "test_case_generator",
            "coverage_validator",
        ]
        result = pipeline.run(design)
        assert isinstance(result, TestDesignResult)
        assert result.conditions  # conditions derived from supplied scenarios
        assert client.call_count == 2

    def test_requirement_document_entry_point_still_works(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        cases_json = json.dumps(
            {
                "test_cases": [
                    {
                        "scenario_id": "SC-001",
                        "condition_id": "COND-001",
                        "requirement_ids": ["REQ-001"],
                        "title": "case",
                        "expected_result": "ok",
                        "steps": [{"action": "do", "expected": "ok"}],
                        "priority": "high",
                        "test_type": "functional",
                    }
                ]
            }
        )
        client = MockLLMClient([ANALYZER, RULES, GAPS, SCENARIOS, CONDITIONS_MANY, cases_json])
        pipeline = build_test_design_pipeline(client, prompts, settings)
        result = pipeline.run(
            RequirementInput(text="Buy one get one on eligible items.", source_name="bogo.md")
        )
        assert "test_condition_analyzer" in pipeline.stage_names
        assert result.conditions
        assert result.test_cases
