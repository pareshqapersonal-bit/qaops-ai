"""CoverageValidator: TestDesignResult -> TestDesignResult (coverage filled).

The first fully deterministic stage of QAOps AI (ADR-015). It embodies
the other half of the founding principle: the LLM generates, and pure
code validates. This stage makes NO LLM calls - its constructor cannot
even accept an LLMClient, so the guarantee is enforced by the type
signature, not merely promised.

Everything here is computed from the traceability graph already present
in the result: requirement/scenario/rule coverage, an aggregate metrics
block, heuristic near-duplicate detection (flags, never deletes -
ADR-007), and invalid-reference detection (which upstream stages should
have made impossible, so a hit is a defect report).
"""

from qaops.core.errors import StageError
from qaops.models import (
    BusinessRuleCoverage,
    CoverageMetrics,
    CoverageReport,
    CoverageStatus,
    DuplicatePair,
    InvalidReference,
    RequirementCoverage,
    ScenarioCategory,
    ScenarioCoverage,
    TestCase,
    TestDesignResult,
    TraceabilityMatrix,
)

# Jaccard overlap on title tokens above which two same-scenario, same-requirement
# test cases are flagged as suspected near-duplicates. Flags for human review
# only; nothing is deleted (ADR-007). A module constant rather than a setting
# until real-corpus calibration justifies a public knob (see ADR-015 discussion).
DUPLICATE_TITLE_OVERLAP_THRESHOLD = 0.7


def _normalized_title(title: str) -> str:
    return " ".join(title.casefold().split())


class CoverageValidator:
    """Computes coverage, traceability, metrics, and duplicate flags."""

    name = "coverage_validator"

    def run(self, data: TestDesignResult) -> TestDesignResult:
        if not data.requirements:
            raise StageError(self.name, "No requirements present; run the analysis pipeline first.")

        invalid_references = self._find_invalid_references(data)
        req_coverage = self._requirement_coverage(data)
        scenario_coverage = self._scenario_coverage(data)
        rule_coverage = self._business_rule_coverage(data, req_coverage)
        traceability = self._traceability(req_coverage)
        duplicates = self._duplicate_pairs(data)
        metrics = self._metrics(data, req_coverage, rule_coverage, scenario_coverage)

        report = CoverageReport(
            per_requirement=req_coverage,
            per_business_rule=rule_coverage,
            per_scenario=scenario_coverage,
            traceability=traceability,
            metrics=metrics,
            duplicate_pairs=duplicates,
            invalid_references=invalid_references,
            suspected_duplicates=[(d.test_case_id_a, d.test_case_id_b) for d in duplicates],
        )
        return data.model_copy(update={"coverage": report})

    # --- reference integrity -------------------------------------------------

    def _find_invalid_references(self, data: TestDesignResult) -> list[InvalidReference]:
        known_scenarios = {s.id for s in data.scenarios}
        known_requirements = {r.id for r in data.requirements}
        invalid: list[InvalidReference] = []
        for tc in data.test_cases:
            if tc.scenario_id not in known_scenarios:
                invalid.append(
                    InvalidReference(
                        test_case_id=tc.id,
                        reference_kind="scenario",
                        missing_id=tc.scenario_id,
                    )
                )
            for rid in tc.requirement_ids:
                if rid not in known_requirements:
                    invalid.append(
                        InvalidReference(
                            test_case_id=tc.id, reference_kind="requirement", missing_id=rid
                        )
                    )
        return invalid

    # --- coverage computations -----------------------------------------------

    def _requirement_coverage(self, data: TestDesignResult) -> list[RequirementCoverage]:
        # Which scenario categories exist per requirement (the expected set).
        expected: dict[str, set[str]] = {r.id: set() for r in data.requirements}
        for sc in data.scenarios:
            for rid in sc.requirement_ids:
                if rid in expected:
                    expected[rid].add(sc.category.value)

        # Which test cases and covered categories reach each requirement.
        cases_by_req: dict[str, list[str]] = {r.id: [] for r in data.requirements}
        covered_cats: dict[str, set[str]] = {r.id: set() for r in data.requirements}
        scenario_category = {s.id: s.category.value for s in data.scenarios}
        for tc in data.test_cases:
            cat = scenario_category.get(tc.scenario_id)
            for rid in tc.requirement_ids:
                if rid in cases_by_req:
                    cases_by_req[rid].append(tc.id)
                    if cat is not None:
                        covered_cats[rid].add(cat)

        result: list[RequirementCoverage] = []
        for req in data.requirements:
            tc_ids = cases_by_req[req.id]
            missing = sorted(expected[req.id] - covered_cats[req.id])
            if not tc_ids:
                status = CoverageStatus.UNCOVERED
            elif missing:
                status = CoverageStatus.PARTIAL
            else:
                status = CoverageStatus.COVERED
            result.append(
                RequirementCoverage(
                    requirement_id=req.id,
                    status=status,
                    test_case_ids=tc_ids,
                    missing_categories=[ScenarioCategory(c) for c in missing],
                )
            )
        return result

    def _scenario_coverage(self, data: TestDesignResult) -> list[ScenarioCoverage]:
        cases_by_scenario: dict[str, list[str]] = {s.id: [] for s in data.scenarios}
        for tc in data.test_cases:
            if tc.scenario_id in cases_by_scenario:
                cases_by_scenario[tc.scenario_id].append(tc.id)
        return [
            ScenarioCoverage(
                scenario_id=s.id,
                status=CoverageStatus.COVERED
                if cases_by_scenario[s.id]
                else CoverageStatus.UNCOVERED,
                test_case_ids=cases_by_scenario[s.id],
            )
            for s in data.scenarios
        ]

    def _business_rule_coverage(
        self, data: TestDesignResult, req_coverage: list[RequirementCoverage]
    ) -> list[BusinessRuleCoverage]:
        # A rule is covered transitively via its requirement's test cases.
        cases_by_req = {rc.requirement_id: rc.test_case_ids for rc in req_coverage}
        result: list[BusinessRuleCoverage] = []
        for rule in data.business_rules:
            tc_ids = cases_by_req.get(rule.requirement_id, [])
            result.append(
                BusinessRuleCoverage(
                    rule_id=rule.id,
                    requirement_id=rule.requirement_id,
                    status=CoverageStatus.COVERED if tc_ids else CoverageStatus.UNCOVERED,
                    test_case_ids=list(tc_ids),
                )
            )
        return result

    def _traceability(self, req_coverage: list[RequirementCoverage]) -> TraceabilityMatrix:
        return TraceabilityMatrix(
            entries={rc.requirement_id: list(rc.test_case_ids) for rc in req_coverage}
        )

    # --- duplicate detection (heuristic; flags, never deletes - ADR-007) -----

    def _duplicate_pairs(self, data: TestDesignResult) -> list[DuplicatePair]:
        pairs: list[DuplicatePair] = []
        cases = data.test_cases
        for i in range(len(cases)):
            for j in range(i + 1, len(cases)):
                reason = self._duplicate_reason(cases[i], cases[j])
                if reason:
                    pairs.append(
                        DuplicatePair(
                            test_case_id_a=cases[i].id,
                            test_case_id_b=cases[j].id,
                            reason=reason,
                        )
                    )
        return pairs

    def _duplicate_reason(self, a: TestCase, b: TestCase) -> str:
        if _normalized_title(a.title) == _normalized_title(b.title):
            return "identical normalized title"
        # Same scenario + same requirement set + high title-token overlap.
        if a.scenario_id == b.scenario_id and set(a.requirement_ids) == set(b.requirement_ids):
            ta = set(_normalized_title(a.title).split())
            tb = set(_normalized_title(b.title).split())
            if ta and tb:
                overlap = len(ta & tb) / len(ta | tb)
                if overlap >= DUPLICATE_TITLE_OVERLAP_THRESHOLD:
                    return f"same scenario and requirements; title overlap {overlap:.0%}"
        return ""

    # --- metrics -------------------------------------------------------------

    def _metrics(
        self,
        data: TestDesignResult,
        req_coverage: list[RequirementCoverage],
        rule_coverage: list[BusinessRuleCoverage],
        scenario_coverage: list[ScenarioCoverage],
    ) -> CoverageMetrics:
        covered = {CoverageStatus.COVERED, CoverageStatus.PARTIAL}
        return CoverageMetrics(
            total_requirements=len(req_coverage),
            covered_requirements=sum(1 for rc in req_coverage if rc.status in covered),
            total_business_rules=len(rule_coverage),
            covered_business_rules=sum(
                1 for bc in rule_coverage if bc.status is CoverageStatus.COVERED
            ),
            total_scenarios=len(scenario_coverage),
            covered_scenarios=sum(
                1 for sc in scenario_coverage if sc.status is CoverageStatus.COVERED
            ),
            total_test_cases=len(data.test_cases),
        )
