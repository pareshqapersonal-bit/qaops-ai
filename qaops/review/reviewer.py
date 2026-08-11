"""QualityReviewer: TestDesignResult -> ReviewReport (ADR-045, Phase 30).

Deterministic, read-only, advisory. The reviewer inspects a COMPLETED run's
TestDesignResult and produces objective quality findings. It CONSUMES the
existing CoverageReport (metrics, per-artifact coverage, duplicate_pairs,
invalid_references, uncovered_* helpers) rather than recomputing coverage, so it
never duplicates CoverageValidator. It adds only net-new interpretive checks the
coverage layer does not already surface.

Guarantees (mirroring the pipeline/agent split of Phases 25-29):
  * pure function of its input: same TestDesignResult -> same ReviewReport;
  * mutates nothing, generates no artifact, invokes no stage/loop/checkpoint;
  * advisory only: findings never gate, fail, or downgrade a run;
  * no feedback into generation.

The future LLM ReviewAgent will consume the ReviewReport this produces to explain
and recommend - it will read these findings, never recompute them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qaops.models import (
    ReviewCategory,
    ReviewFinding,
    ReviewReport,
    ReviewSeverity,
)

if TYPE_CHECKING:
    from qaops.models import TestCase, TestDesignResult

# Default threshold: fraction of conditions left unresolved above which the run is
# flagged for review. Advisory only - it never affects run success. Chosen so the
# healthy BOGO baseline (4/11 = 0.36) does not trip on WARNING severity while a
# pervasive-ambiguity run does; kept conservative and documented in ADR-045.
_UNRESOLVED_WARNING_RATIO = 0.50
_UNRESOLVED_CRITICAL_RATIO = 0.80

# Phase 30 v2 (ADR-045): fraction of test cases that are provisional above which
# the suite is flagged. Advisory only. The healthy BOGO baseline (4/15 = 0.27)
# stays below WARNING; a pervasively-provisional suite trips it.
_PROVISIONAL_WARNING_RATIO = 0.50

# Phase 34 (ADR-049): fraction of test cases carrying at least one assumption
# above which the suite is flagged. Deterministic and quantity-based - severity
# comes from HOW MANY cases depend on unconfirmed facts, never from interpreting
# the free-text assumption strings (which could be setup, product capability, or
# business-rule assumptions - indistinguishable from the text alone). Advisory.
_ASSUMPTION_WARNING_RATIO = 0.50


class QualityReviewer:
    """Produce a deterministic, advisory ReviewReport from a completed result.

    Deterministic and LLM-free by construction: the constructor takes no client,
    so the "no LLM" guarantee is enforced by the type signature, exactly as
    CoverageValidator enforces it for coverage.
    """

    def review(self, result: TestDesignResult) -> ReviewReport:
        """Return the review for a completed run. Never mutates `result`."""
        findings: list[ReviewFinding] = []
        findings.extend(self._unresolved_ratio_findings(result))
        findings.extend(self._uncovered_findings(result))
        findings.extend(self._invalid_reference_findings(result))
        findings.extend(self._duplicate_findings(result))
        findings.extend(self._empty_scenario_findings(result))
        findings.extend(self._provisional_case_findings(result))
        findings.extend(self._truncation_findings(result))
        # Phase 30 v2 (ADR-045): five additional deterministic findings, each
        # reading already-computed data (gap_report, RequirementCoverage, and
        # TestCase fields) - never recomputing coverage.
        findings.extend(self._gap_severity_findings(result))
        findings.extend(self._partial_requirement_findings(result))
        findings.extend(self._provisional_ratio_findings(result))
        findings.extend(self._priority_distribution_findings(result))
        findings.extend(self._type_balance_findings(result))
        # Phase 34 (ADR-049): threshold-gated finding surfacing test-case
        # assumptions (consumes Phase 33's TestCase.assumptions; never categorizes).
        findings.extend(self._assumption_findings(result))

        observations = self._observations(result)
        recommendations = self._recommendations(findings)
        return ReviewReport(
            source_name=result.source_name,
            findings=findings,
            observations=observations,
            recommendations=recommendations,
        )

    # -- findings: consume CoverageReport -----------------------------------

    def _unresolved_ratio_findings(self, result: TestDesignResult) -> list[ReviewFinding]:
        metrics = result.coverage.metrics
        total = metrics.total_conditions
        unresolved = metrics.unresolved_conditions
        if total <= 0 or unresolved <= 0:
            return []
        ratio = unresolved / total
        if ratio >= _UNRESOLVED_CRITICAL_RATIO:
            severity = ReviewSeverity.CRITICAL
        elif ratio >= _UNRESOLVED_WARNING_RATIO:
            severity = ReviewSeverity.WARNING
        else:
            severity = ReviewSeverity.INFO
        pct = round(ratio * 100)
        return [
            ReviewFinding(
                code="unresolved_condition_ratio",
                severity=severity,
                category=ReviewCategory.AMBIGUITY,
                message=(
                    f"{unresolved} of {total} conditions ({pct}%) are unresolved - "
                    "their expected results depend on information not established by the "
                    "requirements."
                ),
                recommendation=(
                    "Review the linked gaps and clarify the underlying requirements so "
                    "these conditions can yield definitive expected results."
                    if severity is not ReviewSeverity.INFO
                    else ""
                ),
            )
        ]

    def _uncovered_findings(self, result: TestDesignResult) -> list[ReviewFinding]:
        coverage = result.coverage
        findings: list[ReviewFinding] = []
        uncovered_reqs = coverage.uncovered_requirement_ids
        if uncovered_reqs:
            findings.append(
                ReviewFinding(
                    code="uncovered_requirements",
                    severity=ReviewSeverity.WARNING,
                    category=ReviewCategory.COVERAGE,
                    message=(f"{len(uncovered_reqs)} requirement(s) have no covering test case."),
                    references=list(uncovered_reqs),
                    recommendation="Add scenarios/cases exercising these requirements.",
                )
            )
        uncovered_rules = coverage.uncovered_business_rule_ids
        if uncovered_rules:
            findings.append(
                ReviewFinding(
                    code="uncovered_business_rules",
                    severity=ReviewSeverity.WARNING,
                    category=ReviewCategory.COVERAGE,
                    message=f"{len(uncovered_rules)} business rule(s) are not covered.",
                    references=list(uncovered_rules),
                    recommendation="Ensure each business rule is validated by a test case.",
                )
            )
        uncovered_scenarios = coverage.uncovered_scenario_ids
        if uncovered_scenarios:
            findings.append(
                ReviewFinding(
                    code="uncovered_scenarios",
                    severity=ReviewSeverity.INFO,
                    category=ReviewCategory.COVERAGE,
                    message=f"{len(uncovered_scenarios)} scenario(s) have no test case.",
                    references=list(uncovered_scenarios),
                )
            )
        return findings

    def _invalid_reference_findings(self, result: TestDesignResult) -> list[ReviewFinding]:
        invalid = result.coverage.invalid_references
        if not invalid:
            return []
        refs = sorted({ref.test_case_id for ref in invalid})
        return [
            ReviewFinding(
                code="broken_references",
                severity=ReviewSeverity.CRITICAL,
                category=ReviewCategory.REFERENCES,
                message=(
                    f"{len(invalid)} test-case reference(s) point to unknown "
                    "scenarios or requirements."
                ),
                references=refs,
                recommendation="These indicate an upstream defect; regenerate affected cases.",
            )
        ]

    def _duplicate_findings(self, result: TestDesignResult) -> list[ReviewFinding]:
        pairs = result.coverage.duplicate_pairs
        if not pairs:
            return []
        refs = sorted({p.test_case_id_a for p in pairs} | {p.test_case_id_b for p in pairs})
        return [
            ReviewFinding(
                code="duplicate_test_cases",
                severity=ReviewSeverity.WARNING,
                category=ReviewCategory.DUPLICATION,
                message=f"{len(pairs)} suspected duplicate test-case pair(s) detected.",
                references=refs,
                recommendation="Consolidate or differentiate the flagged cases.",
            )
        ]

    # -- findings: net-new interpretive checks ------------------------------

    def _empty_scenario_findings(self, result: TestDesignResult) -> list[ReviewFinding]:
        # Scenarios with no conditions AND no test cases: a gap in decomposition
        # depth the coverage layer records per-scenario but does not warn on.
        scenario_ids_with_conditions = {c.scenario_id for c in result.conditions}
        scenario_ids_with_cases = {tc.scenario_id for tc in result.test_cases}
        empty = [
            s.id
            for s in result.scenarios
            if s.id not in scenario_ids_with_conditions and s.id not in scenario_ids_with_cases
        ]
        if not empty:
            return []
        return [
            ReviewFinding(
                code="empty_scenarios",
                severity=ReviewSeverity.WARNING,
                category=ReviewCategory.COMPLETENESS,
                message=f"{len(empty)} scenario(s) produced no conditions or test cases.",
                references=sorted(empty),
                recommendation="Check whether these scenarios are testable as written.",
            )
        ]

    def _provisional_case_findings(self, result: TestDesignResult) -> list[ReviewFinding]:
        # Provisional cases carry placeholder expected results (they validate an
        # unresolved condition). Surfacing their count is a completeness signal;
        # expected_result is a required non-empty field, so we flag provisional
        # status rather than emptiness.
        provisional: list[TestCase] = [tc for tc in result.test_cases if tc.provisional]
        if not provisional:
            return []
        return [
            ReviewFinding(
                code="provisional_test_cases",
                severity=ReviewSeverity.INFO,
                category=ReviewCategory.COMPLETENESS,
                message=(
                    f"{len(provisional)} test case(s) are provisional - their expected "
                    "results are placeholders pending clarification."
                ),
                references=[tc.id for tc in provisional],
            )
        ]

    def _truncation_findings(self, result: TestDesignResult) -> list[ReviewFinding]:
        if not result.expansion_truncated:
            return []
        return [
            ReviewFinding(
                code="expansion_truncated",
                severity=ReviewSeverity.WARNING,
                category=ReviewCategory.COMPLETENESS,
                message=(
                    "Condition expansion was truncated by a generation bound; coverage "
                    "is not exhaustive."
                ),
                recommendation=(result.truncation_note or "Consider raising the expansion bound."),
            )
        ]

    # -- Phase 30 v2 findings ----------------------------------------------

    def _gap_severity_findings(self, result: TestDesignResult) -> list[ReviewFinding]:
        """Blocker/major gaps from the gap_report (ADR-045 v2).

        Reads gap_report.gaps[].severity, which CoverageValidator never touches.
        Blocker gaps are a client-facing red flag (CRITICAL); major gaps warrant
        review (WARNING). Minor gaps are not surfaced as findings.
        """
        from qaops.models.enums import GapSeverity

        blocker = [g for g in result.gap_report.gaps if g.severity is GapSeverity.BLOCKER]
        major = [g for g in result.gap_report.gaps if g.severity is GapSeverity.MAJOR]
        findings: list[ReviewFinding] = []
        if blocker:
            findings.append(
                ReviewFinding(
                    code="blocker_gaps_present",
                    severity=ReviewSeverity.CRITICAL,
                    category=ReviewCategory.AMBIGUITY,
                    message=(
                        f"{len(blocker)} blocker gap(s) in the requirements - core "
                        "behaviour is undefined and must be resolved before handoff."
                    ),
                    references=[g.requirement_id for g in blocker if g.requirement_id],
                    recommendation="Resolve blocker gaps with the product owner before delivery.",
                )
            )
        if major:
            findings.append(
                ReviewFinding(
                    code="major_gaps_present",
                    severity=ReviewSeverity.WARNING,
                    category=ReviewCategory.AMBIGUITY,
                    message=f"{len(major)} major gap(s) in the requirements warrant review.",
                    references=[g.requirement_id for g in major if g.requirement_id],
                    recommendation="Clarify major gaps to firm up affected expected results.",
                )
            )
        return findings

    def _partial_requirement_findings(self, result: TestDesignResult) -> list[ReviewFinding]:
        """Requirements partially covered (ADR-045 v2).

        Consumes RequirementCoverage.status == PARTIAL and its already-computed
        missing_categories. No recomputation - reads CoverageValidator's output.
        """
        from qaops.models.enums import CoverageStatus

        partial = [
            rc for rc in result.coverage.per_requirement if rc.status is CoverageStatus.PARTIAL
        ]
        if not partial:
            return []
        missing_cats = sorted({cat.value for rc in partial for cat in rc.missing_categories})
        detail = f" Missing categories include: {', '.join(missing_cats)}." if missing_cats else ""
        return [
            ReviewFinding(
                code="partial_requirements",
                severity=ReviewSeverity.WARNING,
                category=ReviewCategory.COVERAGE,
                message=(f"{len(partial)} requirement(s) are only partially covered.{detail}"),
                references=[rc.requirement_id for rc in partial],
                recommendation="Add cases for the missing scenario categories per requirement.",
            )
        ]

    def _provisional_ratio_findings(self, result: TestDesignResult) -> list[ReviewFinding]:
        """Provisional test-case ratio above threshold (ADR-045 v2).

        Complements the existing provisional_test_cases (count/INFO) with a
        thresholded ratio: a suite that is mostly provisional is not ready for a
        client. Reads TestCase.provisional; no coverage recompute.
        """
        total = len(result.test_cases)
        if total == 0:
            return []
        provisional = sum(1 for tc in result.test_cases if tc.provisional)
        if provisional == 0:
            return []
        ratio = provisional / total
        if ratio < _PROVISIONAL_WARNING_RATIO:
            return []
        pct = round(ratio * 100)
        return [
            ReviewFinding(
                code="high_provisional_ratio",
                severity=ReviewSeverity.WARNING,
                category=ReviewCategory.COMPLETENESS,
                message=(
                    f"{provisional} of {total} test cases ({pct}%) are provisional - the "
                    "suite is largely placeholder pending clarification."
                ),
                recommendation="Resolve the underlying gaps so provisional cases become concrete.",
            )
        ]

    def _assumption_findings(self, result: TestDesignResult) -> list[ReviewFinding]:
        """Test cases resting on unconfirmed assumptions, above threshold (ADR-049).

        Phase 34. Consumes Phase 33's TestCase.assumptions. Deterministic and
        quantity-based: it counts cases carrying at least one assumption and flags
        the suite only when that fraction crosses the threshold - severity comes
        from HOW MANY cases depend on unconfirmed facts, never from interpreting
        the assumption text (which the reviewer treats as opaque). References list
        the exact case IDs so QA can trace each one. Does not categorize
        assumptions, and does not touch coverage or provisional status.
        """
        total = len(result.test_cases)
        if total == 0:
            return []
        with_assumptions = sorted(tc.id for tc in result.test_cases if tc.assumptions)
        if not with_assumptions:
            return []
        ratio = len(with_assumptions) / total
        if ratio < _ASSUMPTION_WARNING_RATIO:
            return []
        pct = round(ratio * 100)
        return [
            ReviewFinding(
                code="test_case_assumptions",
                severity=ReviewSeverity.WARNING,
                category=ReviewCategory.COMPLETENESS,
                message=(
                    f"{len(with_assumptions)} of {total} test cases ({pct}%) rest on "
                    "unconfirmed assumptions the source does not establish."
                ),
                references=with_assumptions,
                recommendation=(
                    "Review the listed cases' assumptions with the product owner / BA and "
                    "confirm or turn them into requirements."
                ),
            )
        ]

    def _priority_distribution_findings(self, result: TestDesignResult) -> list[ReviewFinding]:
        """Test-priority distribution skew (ADR-045 v2).

        Reads TestCase.priority (CoverageValidator computes no priority data). A
        suite with no critical AND no high cases is a QA-balance concern.
        """
        from qaops.models.enums import Priority

        if not result.test_cases:
            return []
        priorities = {tc.priority for tc in result.test_cases}
        if Priority.CRITICAL not in priorities and Priority.HIGH not in priorities:
            return [
                ReviewFinding(
                    code="priority_distribution_skew",
                    severity=ReviewSeverity.WARNING,
                    category=ReviewCategory.QUALITY,
                    message=(
                        "No test cases are marked critical or high priority - the suite "
                        "lacks prioritisation for the most important paths."
                    ),
                    recommendation="Review priorities so critical paths are marked accordingly.",
                )
            ]
        return []

    def _type_balance_findings(self, result: TestDesignResult) -> list[ReviewFinding]:
        """Test-type balance: positive-only suites (ADR-045 v2).

        Reads TestCase.test_type. A suite with no negative and no boundary cases
        is a classic QA weakness (happy-path only). CoverageValidator computes no
        type distribution.
        """
        from qaops.models.enums import TestType

        if not result.test_cases:
            return []
        types = {tc.test_type for tc in result.test_cases}
        has_negative = TestType.NEGATIVE in types
        has_boundary = TestType.BOUNDARY in types
        if not has_negative and not has_boundary:
            return [
                ReviewFinding(
                    code="missing_negative_boundary_coverage",
                    severity=ReviewSeverity.WARNING,
                    category=ReviewCategory.QUALITY,
                    message=(
                        "The suite contains no negative or boundary test cases - it "
                        "exercises happy paths only."
                    ),
                    recommendation="Add negative and boundary cases to harden the suite.",
                )
            ]
        return []

    # -- narrative-free observations / recommendations ----------------------

    def _observations(self, result: TestDesignResult) -> list[str]:
        m = result.coverage.metrics
        observations = [
            f"{m.total_requirements} requirements, {m.total_business_rules} business rules, "
            f"{m.total_scenarios} scenarios, {m.total_conditions} conditions, "
            f"{m.total_test_cases} test cases.",
            f"Requirement coverage {round(m.requirement_coverage_pct)}%, "
            f"condition coverage {round(m.condition_coverage_pct)}%.",
        ]
        # Phase 30 v2 (ADR-045): neutral distribution counts as observations, so a
        # QA Lead sees the full priority/type breakdown even when no threshold
        # finding fires. Pure counts over TestCase fields; no coverage recompute.
        if result.test_cases:
            observations.append(self._priority_distribution_observation(result))
            observations.append(self._type_distribution_observation(result))
        return observations

    @staticmethod
    def _priority_distribution_observation(result: TestDesignResult) -> str:
        from collections import Counter

        from qaops.models.enums import Priority

        counts = Counter(tc.priority for tc in result.test_cases)
        parts = [f"{p.value} {counts[p]}" for p in Priority if counts[p]]
        return "Priority distribution: " + (", ".join(parts) if parts else "none") + "."

    @staticmethod
    def _type_distribution_observation(result: TestDesignResult) -> str:
        from collections import Counter

        counts = Counter(tc.test_type.value for tc in result.test_cases)
        parts = [f"{name} {counts[name]}" for name in sorted(counts)]
        return "Test-type distribution: " + (", ".join(parts) if parts else "none") + "."

    def _recommendations(self, findings: list[ReviewFinding]) -> list[str]:
        # Deterministic roll-up of the per-finding recommendations, de-duplicated
        # in first-seen order. No LLM, no synthesis beyond collection.
        seen: set[str] = set()
        out: list[str] = []
        for finding in findings:
            rec = finding.recommendation.strip()
            if rec and rec not in seen:
                seen.add(rec)
                out.append(rec)
        return out
