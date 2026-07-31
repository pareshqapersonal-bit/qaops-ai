"""Phase 22 regression suite: condition expansion & ambiguity integrity (ADR-037).

Proves, with MockLLMClient (no live LLM), that TestConditionAnalyzer:
  * derives multiple materially-distinct conditions from one scenario;
  * has no fixed scenario:condition or condition:case ratio;
  * links known gaps to UNRESOLVED conditions (Step 4) so a gap affecting
    testable behaviour cannot coexist with 100% condition coverage;
  * reuses an existing gap rather than duplicating it;
  * leaves informational / non-matching gaps alone;
  * preserves evidence validation, dedup with boundary survival, bounds and
    truncation, and rejects category/behaviour contradictions;
  * still permits a legitimate 1 condition per scenario when the evidence has no
    further dimensions.

Uses a small BOGO/cart-CTA fixture modelled on the production PRD. No BOGO logic
lives in production code; the fixture only scripts model responses.
"""

import json

import pytest

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.llm import MockLLMClient, PromptLoader
from qaops.models import RequirementInput, ScenarioDesignResult
from qaops.models.enums import ConditionStatus, CoverageStatus
from qaops.pipelines.test_design import (
    BusinessRuleExtractor,
    RequirementAnalyzer,
    ScenarioGenerator,
    TestCaseGenerator,
)
from qaops.pipelines.test_design.conditions import TestConditionAnalyzer
from qaops.pipelines.test_design.coverage import CoverageValidator


@pytest.fixture
def settings(tmp_path):  # type: ignore[no-untyped-def]
    return QAOpsSettings(output_dir=tmp_path / "out")


@pytest.fixture
def prompts() -> PromptLoader:
    return PromptLoader()


# --- BOGO/cart-CTA fixture: requirements, rules, ONE gap affecting copy -------

ANALYZER = json.dumps(
    {
        "requirements": [
            {"title": "B1G1 tag", "description": "Show a tag for Buy 1 Get 1 eligible cart items."},
            {
                "title": "Second offer tag",
                "description": "Show a tag for 'Buy 2 get 50% on 2nd' eligible items.",
            },
            {
                "title": "Conditional CTA",
                "description": "Show the CTA only when an eligible mapping is available.",
            },
        ]
    }
)
RULES = json.dumps(
    {
        "rules": [
            {
                "requirement_id": "REQ-001",
                "rule": "Tag copy is exactly 'Eligible for Buy 1 Get 1'.",
                "source_excerpt": "Tag: Eligible for Buy 1 Get 1",
            },
            {
                "requirement_id": "REQ-003",
                "rule": "CTA shown only when eligible and PLP mapping available.",
                "source_excerpt": "Show the CTA only when eligible PLP mapping is available",
            },
        ]
    }
)
# One gap: the exact tag copy for the SECOND offer (REQ-002) is unspecified.
GAPS = json.dumps(
    {
        "gaps": [
            {
                "description": "The exact tag copy for the second offer is not specified.",
                "severity": "major",
                "requirement_id": "REQ-002",
                "suggested_question": "What is the exact tag copy text for the second offer?",
            }
        ]
    }
)
# Behaviour-level scenarios (post-Phase-22 granularity): few, broad.
SCENARIOS = json.dumps(
    {
        "scenarios": [
            {
                "title": "B1G1 tag display",
                "description": "Tag shown/hidden for B1G1 by eligibility, with exact copy.",
                "category": "functional",
                "requirement_ids": ["REQ-001"],
            },
            {
                "title": "Second offer tag display",
                "description": "Tag shown for the 'Buy 2 get 50%' offer.",
                "category": "functional",
                "requirement_ids": ["REQ-002"],
            },
            {
                "title": "CTA visibility by mapping",
                "description": "CTA shown only when eligible and a PLP mapping exists.",
                "category": "state_transition",
                "requirement_ids": ["REQ-003"],
            },
        ]
    }
)


def _scenario_design() -> ScenarioDesignResult:
    settings = QAOpsSettings()
    prompts = PromptLoader()
    a = RequirementAnalyzer(MockLLMClient([ANALYZER]), prompts, settings).run(
        RequirementInput(text="Cart CTA for B1G1 eligible products.", source_name="bogo.pdf")
    )
    enriched = BusinessRuleExtractor(MockLLMClient([RULES]), prompts, settings).run(a)
    # Inject the gap by re-running through a gap-carrying analysis: the scenario
    # generator preserves analysis.gap_report, so attach gaps via a mock gap stage.
    from qaops.pipelines.test_design.gaps import GapAnalyzer

    withgaps = GapAnalyzer(MockLLMClient([GAPS]), prompts, settings).run(enriched)
    return ScenarioGenerator(MockLLMClient([SCENARIOS]), prompts, settings).run(withgaps)


def _analyze(settings, prompts, conditions_response):  # type: ignore[no-untyped-def]
    return TestConditionAnalyzer(MockLLMClient([conditions_response]), prompts, settings).run(
        _scenario_design()
    )


# Multiple conditions from ONE scenario (SC-003 CTA decision table) + a copy
# condition (SC-002) that a gap should force unresolved.
CONDITIONS = json.dumps(
    {
        "conditions": [
            # SC-001: positive + negative + exact copy (copy documented for offer 1)
            {
                "scenario_id": "SC-001",
                "requirement_ids": ["REQ-001"],
                "business_rule_ids": ["BR-001"],
                "category": "positive",
                "description": "B1G1-eligible item shows the tag.",
                "rationale": "REQ-001",
                "source_basis": "explicit_requirement",
                "status": "resolved",
                "parameters": {"eligibility": "eligible"},
                "gap_reference": "",
            },
            {
                "scenario_id": "SC-001",
                "requirement_ids": ["REQ-001"],
                "business_rule_ids": [],
                "category": "negative",
                "description": "Ineligible item does not show the B1G1 tag.",
                "rationale": "REQ-001",
                "source_basis": "explicit_requirement",
                "status": "resolved",
                "parameters": {"eligibility": "ineligible"},
                "gap_reference": "",
            },
            {
                "scenario_id": "SC-001",
                "requirement_ids": ["REQ-001"],
                "business_rule_ids": ["BR-001"],
                "category": "validation",
                "description": "B1G1 tag copy reads exactly 'Eligible for Buy 1 Get 1'.",
                "rationale": "BR-001 exact copy",
                "source_basis": "explicit_rule",
                "status": "resolved",
                "parameters": {"copy": "exact"},
                "gap_reference": "",
            },
            # SC-002: second-offer exact copy -> should be forced UNRESOLVED by the REQ-002 gap
            {
                "scenario_id": "SC-002",
                "requirement_ids": ["REQ-002"],
                "business_rule_ids": [],
                "category": "validation",
                "description": "Second-offer tag copy wording matches the exact specified format.",
                "rationale": "Second offer tag",
                "source_basis": "explicit_requirement",
                "status": "resolved",
                "parameters": {"copy": "exact"},
                "gap_reference": "",
            },
            {
                "scenario_id": "SC-002",
                "requirement_ids": ["REQ-002"],
                "business_rule_ids": [],
                "category": "positive",
                "description": "Second-offer-eligible item shows its tag.",
                "rationale": "Second offer tag appears",
                "source_basis": "explicit_requirement",
                "status": "resolved",
                "parameters": {"eligibility": "eligible"},
                "gap_reference": "",
            },
            # SC-003: eligibility x mapping decision table (3 documented outcomes)
            {
                "scenario_id": "SC-003",
                "requirement_ids": ["REQ-003"],
                "business_rule_ids": ["BR-002"],
                "category": "combination",
                "description": "Eligible item with mapping present shows the CTA.",
                "rationale": "BR-002 combination",
                "source_basis": "documented_combination",
                "status": "resolved",
                "parameters": {"eligibility": "eligible", "mapping": "present"},
                "gap_reference": "",
            },
            {
                "scenario_id": "SC-003",
                "requirement_ids": ["REQ-003"],
                "business_rule_ids": ["BR-002"],
                "category": "combination",
                "description": "Eligible item with mapping absent hides the CTA.",
                "rationale": "BR-002 combination",
                "source_basis": "documented_combination",
                "status": "resolved",
                "parameters": {"eligibility": "eligible", "mapping": "absent"},
                "gap_reference": "",
            },
            {
                "scenario_id": "SC-003",
                "requirement_ids": ["REQ-003"],
                "business_rule_ids": ["BR-002"],
                "category": "negative",
                "description": "Ineligible item does not show the CTA.",
                "rationale": "BR-002",
                "source_basis": "documented_combination",
                "status": "resolved",
                "parameters": {"eligibility": "ineligible"},
                "gap_reference": "",
            },
        ]
    }
)


class TestGenuineExpansion:
    def test_one_scenario_yields_multiple_conditions(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        result = _analyze(settings, prompts, CONDITIONS)
        by_sc: dict[str, int] = {}
        for c in result.conditions:
            by_sc[c.scenario_id] = by_sc.get(c.scenario_id, 0) + 1
        assert by_sc["SC-001"] == 3  # positive + negative + copy
        assert by_sc["SC-003"] == 3  # decision-table combinations

    def test_no_fixed_scenario_condition_ratio(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        result = _analyze(settings, prompts, CONDITIONS)
        assert len(result.scenario_design.scenarios) == 3
        assert len(result.conditions) == 8  # not 3

    def test_positive_and_negative_are_separate_conditions(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        result = _analyze(settings, prompts, CONDITIONS)
        cats = {(c.scenario_id, c.category.value) for c in result.conditions}
        assert ("SC-001", "positive") in cats
        assert ("SC-001", "negative") in cats

    def test_decision_table_expands_by_documented_combinations(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        result = _analyze(settings, prompts, CONDITIONS)
        sc3 = [c for c in result.conditions if c.scenario_id == "SC-003"]
        params = {tuple(sorted(c.parameters.items())) for c in sc3}
        assert (("eligibility", "eligible"), ("mapping", "absent")) in params
        assert (("eligibility", "eligible"), ("mapping", "present")) in params


class TestGapToUnresolvedIntegration:
    def test_gap_forces_matching_condition_unresolved(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        result = _analyze(settings, prompts, CONDITIONS)
        copy = next(
            c
            for c in result.conditions
            if c.scenario_id == "SC-002" and "copy" in c.description.lower()
        )
        assert copy.status is ConditionStatus.UNRESOLVED
        assert copy.gap_reference  # linked to the gap text

    def test_visibility_condition_on_same_requirement_stays_resolved(
        self, settings, prompts
    ) -> None:  # type: ignore[no-untyped-def]
        result = _analyze(settings, prompts, CONDITIONS)
        vis = next(
            c
            for c in result.conditions
            if c.scenario_id == "SC-002" and "shows its tag" in c.description.lower()
        )
        # Same requirement REQ-002, but subject is visibility not copy -> resolved.
        assert vis.status is ConditionStatus.RESOLVED

    def test_documented_copy_condition_stays_resolved(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        result = _analyze(settings, prompts, CONDITIONS)
        # SC-001 copy IS documented (BR-001) and REQ-001 has no gap -> resolved.
        c1 = next(
            c
            for c in result.conditions
            if c.scenario_id == "SC-001" and "copy" in c.description.lower()
        )
        assert c1.status is ConditionStatus.RESOLVED

    def test_existing_gap_is_reused_not_duplicated(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        result = _analyze(settings, prompts, CONDITIONS)
        gaps = result.scenario_design.analysis.gap_report.gaps
        copy_gaps = [
            g for g in gaps if "copy" in g.description.lower() and g.requirement_id == "REQ-002"
        ]
        # The original REQ-002 copy gap is reused for the unresolved condition;
        # no duplicate gap is synthesized for the same ambiguity.
        assert len(copy_gaps) == 1


class TestCoverageIntegrity:
    def test_unresolved_condition_drops_condition_coverage_below_100(
        self, settings, prompts
    ) -> None:  # type: ignore[no-untyped-def]
        cond_result = _analyze(settings, prompts, CONDITIONS)
        # one case per condition
        cases = []
        for c in cond_result.conditions:
            cases.append(
                {
                    "scenario_id": c.scenario_id,
                    "condition_id": c.id,
                    "requirement_ids": c.requirement_ids or ["REQ-001"],
                    "title": f"case {c.id}",
                    "expected_result": "documented"
                    if c.status.value == "resolved"
                    else "unknown - see gap",
                    "steps": [{"action": "do", "expected": "ok"}],
                    "priority": "high",
                    "test_type": "functional",
                }
            )
        final = TestCaseGenerator(
            MockLLMClient([json.dumps({"test_cases": cases})]), prompts, settings
        ).run(cond_result)
        covered = CoverageValidator().run(final)
        m = covered.coverage.metrics
        assert m.unresolved_conditions == 1
        assert m.covered_conditions == m.total_conditions - 1
        assert m.condition_coverage_pct < 100.0

    def test_provisional_case_does_not_cover_unresolved_condition(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        cond_result = _analyze(settings, prompts, CONDITIONS)
        cases = [
            {
                "scenario_id": c.scenario_id,
                "condition_id": c.id,
                "requirement_ids": c.requirement_ids or ["REQ-001"],
                "title": f"case {c.id}",
                "expected_result": "x",
                "steps": [{"action": "do", "expected": "ok"}],
                "priority": "high",
                "test_type": "functional",
            }
            for c in cond_result.conditions
        ]
        final = TestCaseGenerator(
            MockLLMClient([json.dumps({"test_cases": cases})]), prompts, settings
        ).run(cond_result)
        covered = CoverageValidator().run(final)
        # The unresolved condition has a (provisional) case but is NOT covered.
        unresolved = [c for c in covered.conditions if c.status.value == "unresolved"]
        assert len(unresolved) == 1
        per = {cc.condition_id: cc for cc in covered.coverage.per_condition}
        assert per[unresolved[0].id].status is CoverageStatus.UNCOVERED
        prov = [t for t in final.test_cases if t.condition_id == unresolved[0].id]
        assert prov and all(t.provisional for t in prov)


class TestDeterministicGuards:
    def test_category_behaviour_contradiction_is_rejected(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        bad = json.dumps(
            {
                "conditions": [
                    {
                        "scenario_id": "SC-001",
                        "requirement_ids": ["REQ-001"],
                        "business_rule_ids": [],
                        "category": "negative",
                        "description": "Item is not eligible when the criteria are met.",
                        "rationale": "x",
                        "source_basis": "explicit_requirement",
                        "status": "resolved",
                        "parameters": {},
                        "gap_reference": "",
                    }
                ]
            }
        )
        with pytest.raises(StageError, match="contradictory"):
            _analyze(settings, prompts, bad)

    def test_evidence_validation_still_rejects_unknown_scenario(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        bad = json.dumps(
            {
                "conditions": [
                    {
                        "scenario_id": "SC-099",
                        "requirement_ids": ["REQ-001"],
                        "business_rule_ids": [],
                        "category": "positive",
                        "description": "x",
                        "rationale": "x",
                        "source_basis": "explicit_requirement",
                        "status": "resolved",
                        "parameters": {},
                        "gap_reference": "",
                    }
                ]
            }
        )
        with pytest.raises(StageError, match="SC-099"):
            _analyze(settings, prompts, bad)

    def test_derived_without_evidence_still_rejected(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
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
                        "parameters": {"n": "2"},
                        "gap_reference": "",
                    }
                ]
            }
        )
        with pytest.raises(StageError, match="cites no requirement or business rule"):
            _analyze(settings, prompts, bad)


class TestDedupAndBounds:
    def test_paraphrased_duplicates_collapse(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        dup = json.dumps(
            {
                "conditions": [
                    {
                        "scenario_id": "SC-003",
                        "requirement_ids": ["REQ-003"],
                        "business_rule_ids": ["BR-002"],
                        "category": "combination",
                        "description": "Eligible with mapping absent hides the CTA.",
                        "rationale": "x",
                        "source_basis": "documented_combination",
                        "status": "resolved",
                        "parameters": {"eligibility": "eligible", "mapping": "absent"},
                        "gap_reference": "",
                    },
                    {
                        "scenario_id": "SC-003",
                        "requirement_ids": ["REQ-003"],
                        "business_rule_ids": ["BR-002"],
                        "category": "combination",
                        "description": "Hide CTA when no mapping exists for eligible item.",
                        "rationale": "x",
                        "source_basis": "documented_combination",
                        "status": "resolved",
                        "parameters": {"eligibility": "eligible", "mapping": "absent"},
                        "gap_reference": "",
                    },
                ]
            }
        )
        result = _analyze(settings, prompts, dup)
        # Same scenario+category+basis+refs+params -> one condition.
        assert len(result.conditions) == 1

    def test_meaningful_variants_survive_dedup(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        result = _analyze(settings, prompts, CONDITIONS)
        sc3 = [c for c in result.conditions if c.scenario_id == "SC-003"]
        mappings = {c.parameters.get("mapping") for c in sc3 if "mapping" in c.parameters}
        assert {"present", "absent"} <= mappings

    def test_bound_truncates_and_flags(self, tmp_path, prompts) -> None:  # type: ignore[no-untyped-def]
        settings = QAOpsSettings(output_dir=tmp_path / "o", max_conditions_per_scenario=1)
        result = _analyze(settings, prompts, CONDITIONS)
        assert result.expansion_truncated is True
        assert "limit reached" in result.truncation_note.lower()


class TestLegitimateOneToOne:
    def test_single_dimension_scenario_may_produce_one_condition(self, settings, prompts) -> None:  # type: ignore[no-untyped-def]
        # A scenario whose evidence has exactly one testable dimension SHOULD
        # produce one condition - SC==COND is correct here, not a bug.
        one = json.dumps(
            {
                "conditions": [
                    {
                        "scenario_id": "SC-001",
                        "requirement_ids": ["REQ-001"],
                        "business_rule_ids": [],
                        "category": "positive",
                        "description": "B1G1-eligible item shows the tag.",
                        "rationale": "REQ-001",
                        "source_basis": "explicit_requirement",
                        "status": "resolved",
                        "parameters": {},
                        "gap_reference": "",
                    },
                ]
            }
        )
        result = _analyze(settings, prompts, one)
        assert len(result.conditions) == 1
        assert result.conditions[0].status is ConditionStatus.RESOLVED
