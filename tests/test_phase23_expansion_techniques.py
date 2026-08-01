"""Phase 23 regression suite: technique-driven test-case expansion (ADR-038).

Proves the deterministic ExpansionPlanner turns each condition's already-derived
technique into the correct set of variant slots, that the generator authors one
case per slot with durable traceability (slot_id + technique), and that all
Phase 21/22 guarantees survive: no invented behaviour, dedup across slots,
unresolved -> single provisional slot, bounds/truncation, and evidence-bound
generation.

Two layers of test:
  * planner unit tests (pure, deterministic) for every supported technique;
  * end-to-end tests driving the real TestCaseGenerator with MockLLMClient,
    proving the plan shapes the authored cases.
No BOGO logic lives in production code; fixtures only script model responses.
"""

import json

import pytest

from qaops.config import QAOpsSettings
from qaops.llm import MockLLMClient, PromptLoader
from qaops.models import (
    RequirementInput,
    ScenarioDesignResult,
    TestCondition,
)
from qaops.models.enums import ConditionCategory, ConditionStatus, SourceBasis
from qaops.pipelines.test_design import (
    BusinessRuleExtractor,
    RequirementAnalyzer,
    ScenarioGenerator,
    TestCaseGenerator,
)
from qaops.pipelines.test_design.conditions import TestConditionAnalyzer
from qaops.pipelines.test_design.coverage import CoverageValidator
from qaops.pipelines.test_design.expansion import ExpansionPlanner


@pytest.fixture
def settings(tmp_path):  # type: ignore[no-untyped-def]
    return QAOpsSettings(output_dir=tmp_path / "out")


@pytest.fixture
def prompts() -> PromptLoader:
    return PromptLoader()


def _cond(
    cat,
    params,
    status=ConditionStatus.RESOLVED,
    cid="COND-001",
    sid="SC-001",
    basis=SourceBasis.EXPLICIT_RULE,
):  # type: ignore[no-untyped-def]
    return TestCondition(
        id=cid,
        scenario_id=sid,
        requirement_ids=["REQ-001"],
        business_rule_ids=["BR-001"],
        category=cat,
        description="documented behaviour",
        source_basis=basis,
        status=status,
        parameters=params,
    )


# ===========================================================================
# Planner unit tests - one per supported technique
# ===========================================================================


class TestPlannerBoundary:
    def test_numeric_threshold_yields_below_at_above(self) -> None:
        plan = ExpansionPlanner(15).plan([_cond(ConditionCategory.BOUNDARY, {"quantity": "2"})])
        slots = plan.per_condition[0].slots
        assert [s.variant_label for s in slots] == [
            "below_boundary",
            "at_boundary",
            "above_boundary",
        ]
        assert [s.parameter_delta["quantity"] for s in slots] == ["1", "2", "3"]
        assert all(s.technique == "boundary" for s in slots)

    def test_no_numeric_param_does_not_invent_neighbours(self) -> None:
        # No documented number -> a single at-boundary slot, never fabricated 1/3.
        plan = ExpansionPlanner(15).plan([_cond(ConditionCategory.BOUNDARY, {})])
        slots = plan.per_condition[0].slots
        assert len(slots) == 1
        assert slots[0].variant_label == "at_boundary"

    def test_slots_carry_reason_and_slot_id(self) -> None:
        plan = ExpansionPlanner(15).plan([_cond(ConditionCategory.BOUNDARY, {"n": "5"})])
        slots = plan.per_condition[0].slots
        assert [s.slot_id for s in slots] == ["COND-001-S1", "COND-001-S2", "COND-001-S3"]
        assert all(s.reason for s in slots)


class TestPlannerEquivalence:
    def test_documented_partitions_yield_one_slot_each(self) -> None:
        plan = ExpansionPlanner(15).plan(
            [_cond(ConditionCategory.EQUIVALENCE, {"class": "valid|invalid|expired"})]
        )
        slots = plan.per_condition[0].slots
        assert len(slots) == 3
        assert {s.parameter_delta["class"] for s in slots} == {"valid", "invalid", "expired"}

    def test_single_class_yields_one_representative(self) -> None:
        plan = ExpansionPlanner(15).plan([_cond(ConditionCategory.EQUIVALENCE, {"class": "valid"})])
        slots = plan.per_condition[0].slots
        assert len(slots) == 1
        assert slots[0].variant_label == "representative"


class TestPlannerDecisionTableCombination:
    def test_combination_condition_is_single_documented_row(self) -> None:
        # A COMBINATION condition already represents ONE documented decision-table
        # row (the analyzer emits one condition per row), so it expands to one
        # case - the Cartesian guard lives in the analyzer, not here.
        plan = ExpansionPlanner(15).plan(
            [
                _cond(
                    ConditionCategory.COMBINATION,
                    {"eligibility": "eligible", "mapping": "present"},
                    basis=SourceBasis.DOCUMENTED_COMBINATION,
                )
            ]
        )
        slots = plan.per_condition[0].slots
        assert len(slots) == 1
        assert slots[0].parameter_delta == {"eligibility": "eligible", "mapping": "present"}


class TestPlannerStateTransition:
    def test_documented_transitions_yield_one_slot_each(self) -> None:
        plan = ExpansionPlanner(15).plan(
            [
                _cond(
                    ConditionCategory.STATE_TRANSITION,
                    {"transitions": "draft->submitted, submitted->approved"},
                )
            ]
        )
        slots = plan.per_condition[0].slots
        assert [s.variant_label for s in slots] == ["draft->submitted", "submitted->approved"]

    def test_single_transition_yields_one_slot(self) -> None:
        plan = ExpansionPlanner(15).plan(
            [_cond(ConditionCategory.STATE_TRANSITION, {"transition": "open->closed"})]
        )
        assert len(plan.per_condition[0].slots) == 1


class TestPlannerPositiveNegative:
    def test_positive_is_single_slot(self) -> None:
        plan = ExpansionPlanner(15).plan([_cond(ConditionCategory.POSITIVE, {})])
        slots = plan.per_condition[0].slots
        assert len(slots) == 1
        assert slots[0].technique == "positive"

    def test_negative_is_single_slot(self) -> None:
        plan = ExpansionPlanner(15).plan([_cond(ConditionCategory.NEGATIVE, {})])
        assert len(plan.per_condition[0].slots) == 1


class TestPlannerDataRoleVariation:
    def test_data_variation_expands_by_documented_values(self) -> None:
        plan = ExpansionPlanner(15).plan(
            [_cond(ConditionCategory.DATA_VARIATION, {"offer": "b1g1|buy2get50"})]
        )
        slots = plan.per_condition[0].slots
        assert len(slots) == 2
        assert {s.parameter_delta["offer"] for s in slots} == {"b1g1", "buy2get50"}

    def test_role_variation_expands_by_documented_roles(self) -> None:
        plan = ExpansionPlanner(15).plan(
            [_cond(ConditionCategory.ROLE_VARIATION, {"role": "guest|member"})]
        )
        assert len(plan.per_condition[0].slots) == 2

    def test_data_variation_single_value_is_one_slot(self) -> None:
        plan = ExpansionPlanner(15).plan(
            [_cond(ConditionCategory.DATA_VARIATION, {"offer": "b1g1"})]
        )
        assert len(plan.per_condition[0].slots) == 1


class TestPlannerUnresolved:
    def test_unresolved_is_single_provisional_slot(self) -> None:
        # Even a boundary condition, if unresolved, must NOT fan out - we do not
        # imply we know the below/at/above outcomes when behaviour is undocumented.
        plan = ExpansionPlanner(15).plan(
            [
                _cond(
                    ConditionCategory.BOUNDARY, {"quantity": "2"}, status=ConditionStatus.UNRESOLVED
                )
            ]
        )
        slots = plan.per_condition[0].slots
        assert len(slots) == 1
        assert slots[0].technique == "provisional"


class TestPlannerBounds:
    def test_slots_capped_at_max(self) -> None:
        plan = ExpansionPlanner(2).plan(
            [_cond(ConditionCategory.EQUIVALENCE, {"class": "a|b|c|d|e"})]
        )
        assert len(plan.per_condition[0].slots) == 2  # capped from 5


# ===========================================================================
# End-to-end tests - the plan shapes the authored cases
# ===========================================================================


def _design(analyzer_reqs, rules, scenarios) -> ScenarioDesignResult:  # type: ignore[no-untyped-def]
    s = QAOpsSettings()
    p = PromptLoader()
    a = RequirementAnalyzer(MockLLMClient([analyzer_reqs]), p, s).run(
        RequirementInput(text="doc", source_name="x.md")
    )
    e = BusinessRuleExtractor(MockLLMClient([rules]), p, s).run(a)
    return ScenarioGenerator(MockLLMClient([scenarios]), p, s).run(e)


ANALYZER = json.dumps(
    {"requirements": [{"title": "R", "description": "Behaviour when quantity >= 2."}]}
)
RULES = json.dumps(
    {
        "rules": [
            {
                "requirement_id": "REQ-001",
                "rule": "Applies when quantity >= 2.",
                "source_excerpt": "quantity >= 2",
            }
        ]
    }
)
SCEN = json.dumps(
    {
        "scenarios": [
            {
                "title": "Threshold",
                "description": "qty",
                "category": "positive",
                "requirement_ids": ["REQ-001"],
            }
        ]
    }
)


def _author_from_plan(cond_result, settings, prompts):  # type: ignore[no-untyped-def]
    """Author exactly one case per planned slot (what a compliant model does)."""
    plan = ExpansionPlanner(settings.max_cases_per_condition).plan(list(cond_result.conditions))
    cases = []
    for cp in plan.per_condition:
        for slot in cp.slots:
            cases.append(
                {
                    "scenario_id": cp.scenario_id,
                    "condition_id": cp.condition_id,
                    "slot_id": slot.slot_id,
                    "requirement_ids": ["REQ-001"],
                    "title": f"{cp.condition_id} {slot.variant_label}",
                    "expected_result": "documented outcome"
                    if slot.technique != "provisional"
                    else "confirm with product owner",
                    "steps": [{"action": f"apply {slot.parameter_delta}", "expected": "ok"}],
                    "test_data": dict(slot.parameter_delta),
                    "priority": "high",
                    "test_type": "boundary" if slot.technique == "boundary" else "functional",
                    "tags": [slot.technique],
                }
            )
    return TestCaseGenerator(
        MockLLMClient([json.dumps({"test_cases": cases})]), prompts, settings
    ).run(cond_result)


class TestEndToEndExpansion:
    def test_boundary_condition_yields_three_traceable_cases(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        design = _design(ANALYZER, RULES, SCEN)
        conds = json.dumps(
            {
                "conditions": [
                    {
                        "scenario_id": "SC-001",
                        "requirement_ids": ["REQ-001"],
                        "business_rule_ids": ["BR-001"],
                        "category": "boundary",
                        "description": "Quantity at threshold.",
                        "rationale": "BR-001",
                        "source_basis": "derived_boundary",
                        "status": "resolved",
                        "parameters": {"quantity": "2"},
                        "gap_reference": "",
                    }
                ]
            }
        )
        cond_result = TestConditionAnalyzer(MockLLMClient([conds]), prompts, settings).run(design)
        final = _author_from_plan(cond_result, settings, prompts)
        assert len(final.test_cases) == 3
        # traceability: every case carries slot_id + technique
        assert all(tc.slot_id and tc.technique == "boundary" for tc in final.test_cases)
        # distinct data drives distinct cases
        assert {tc.test_data["quantity"] for tc in final.test_cases} == {"1", "2", "3"}

    def test_equivalence_condition_yields_one_case_per_partition(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        design = _design(ANALYZER, RULES, SCEN)
        conds = json.dumps(
            {
                "conditions": [
                    {
                        "scenario_id": "SC-001",
                        "requirement_ids": ["REQ-001"],
                        "business_rule_ids": ["BR-001"],
                        "category": "equivalence",
                        "description": "Item class partitions.",
                        "rationale": "BR-001",
                        "source_basis": "derived_equivalence",
                        "status": "resolved",
                        "parameters": {"class": "eligible|ineligible"},
                        "gap_reference": "",
                    }
                ]
            }
        )
        cond_result = TestConditionAnalyzer(MockLLMClient([conds]), prompts, settings).run(design)
        final = _author_from_plan(cond_result, settings, prompts)
        assert len(final.test_cases) == 2
        assert {tc.test_data["class"] for tc in final.test_cases} == {"eligible", "ineligible"}

    def test_unresolved_condition_yields_single_provisional_case(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        design = _design(ANALYZER, RULES, SCEN)
        conds = json.dumps(
            {
                "conditions": [
                    {
                        "scenario_id": "SC-001",
                        "requirement_ids": ["REQ-001"],
                        "business_rule_ids": [],
                        "category": "boundary",
                        "description": "Undocumented boundary behaviour.",
                        "rationale": "x",
                        "source_basis": "scenario",
                        "status": "unresolved",
                        "parameters": {"quantity": "2"},
                        "gap_reference": "Threshold behaviour is unspecified.",
                    }
                ]
            }
        )
        cond_result = TestConditionAnalyzer(MockLLMClient([conds]), prompts, settings).run(design)
        final = _author_from_plan(cond_result, settings, prompts)
        # single slot despite being a boundary, and the case is provisional
        assert len(final.test_cases) == 1
        assert final.test_cases[0].provisional is True

    def test_positive_condition_stays_one_case(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        design = _design(ANALYZER, RULES, SCEN)
        conds = json.dumps(
            {
                "conditions": [
                    {
                        "scenario_id": "SC-001",
                        "requirement_ids": ["REQ-001"],
                        "business_rule_ids": [],
                        "category": "positive",
                        "description": "Happy path.",
                        "rationale": "x",
                        "source_basis": "explicit_requirement",
                        "status": "resolved",
                        "parameters": {},
                        "gap_reference": "",
                    }
                ]
            }
        )
        cond_result = TestConditionAnalyzer(MockLLMClient([conds]), prompts, settings).run(design)
        final = _author_from_plan(cond_result, settings, prompts)
        assert len(final.test_cases) == 1  # legitimate 1:1

    def test_dedup_collapses_identical_slot_output(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        # If the model authored two identical cases for the same boundary point,
        # the canonical-signature dedup collapses them (same condition + data).
        design = _design(ANALYZER, RULES, SCEN)
        conds = json.dumps(
            {
                "conditions": [
                    {
                        "scenario_id": "SC-001",
                        "requirement_ids": ["REQ-001"],
                        "business_rule_ids": ["BR-001"],
                        "category": "boundary",
                        "description": "At threshold.",
                        "rationale": "BR-001",
                        "source_basis": "derived_boundary",
                        "status": "resolved",
                        "parameters": {"quantity": "2"},
                        "gap_reference": "",
                    }
                ]
            }
        )
        cond_result = TestConditionAnalyzer(MockLLMClient([conds]), prompts, settings).run(design)
        dup_cases = {
            "test_cases": [
                {
                    "scenario_id": "SC-001",
                    "condition_id": "COND-001",
                    "slot_id": "COND-001-S2",
                    "requirement_ids": ["REQ-001"],
                    "title": "at threshold",
                    "expected_result": "applies",
                    "steps": [{"action": "qty 2", "expected": "ok"}],
                    "test_data": {"quantity": "2"},
                    "priority": "high",
                    "test_type": "boundary",
                    "tags": ["boundary"],
                },
                {
                    "scenario_id": "SC-001",
                    "condition_id": "COND-001",
                    "slot_id": "COND-001-S2",
                    "requirement_ids": ["REQ-001"],
                    "title": "AT threshold restated",
                    "expected_result": "applies",
                    "steps": [{"action": "qty 2", "expected": "ok"}],
                    "test_data": {"quantity": "2"},
                    "priority": "high",
                    "test_type": "boundary",
                    "tags": ["boundary"],
                },
            ]
        }
        final = TestCaseGenerator(MockLLMClient([json.dumps(dup_cases)]), prompts, settings).run(
            cond_result
        )
        assert len(final.test_cases) == 1

    def test_coverage_and_traceability_preserved(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        design = _design(ANALYZER, RULES, SCEN)
        conds = json.dumps(
            {
                "conditions": [
                    {
                        "scenario_id": "SC-001",
                        "requirement_ids": ["REQ-001"],
                        "business_rule_ids": ["BR-001"],
                        "category": "boundary",
                        "description": "Quantity at threshold.",
                        "rationale": "BR-001",
                        "source_basis": "derived_boundary",
                        "status": "resolved",
                        "parameters": {"quantity": "2"},
                        "gap_reference": "",
                    }
                ]
            }
        )
        cond_result = TestConditionAnalyzer(MockLLMClient([conds]), prompts, settings).run(design)
        final = _author_from_plan(cond_result, settings, prompts)
        covered = CoverageValidator().run(final)
        # condition covered by >=1 non-provisional case; 3 cases trace to 1 condition
        assert covered.coverage.metrics.covered_conditions == 1
        assert all(tc.condition_id == "COND-001" for tc in covered.test_cases)
        assert all(tc.scenario_id == "SC-001" for tc in covered.test_cases)
