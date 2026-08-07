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

    # -- narrative-free observations / recommendations ----------------------

    def _observations(self, result: TestDesignResult) -> list[str]:
        m = result.coverage.metrics
        return [
            f"{m.total_requirements} requirements, {m.total_business_rules} business rules, "
            f"{m.total_scenarios} scenarios, {m.total_conditions} conditions, "
            f"{m.total_test_cases} test cases.",
            f"Requirement coverage {round(m.requirement_coverage_pct)}%, "
            f"condition coverage {round(m.condition_coverage_pct)}%.",
        ]

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
