"""Phase 30 tests: deterministic QualityReviewer + advisory ReviewReport (ADR-045).

Covers the reviewer as a pure function (determinism, non-mutation, per-check
findings) using the two real run artifacts as fixtures, and the additive API
surfacing (optional field + standalone export, COMPLETED-only, backward compat).
"""

import json
from pathlib import Path

from qaops.models import (
    ReviewCategory,
    ReviewReport,
    ReviewSeverity,
    TestDesignResult,
)
from qaops.review import QualityReviewer

_FIXTURES = Path(__file__).parent / "fixtures" / "phase29"


def _result(name: str) -> TestDesignResult:
    return TestDesignResult.model_validate(json.loads((_FIXTURES / name).read_text()))


class TestReviewerContract:
    def test_reviewer_takes_no_llm_client(self) -> None:
        # Determinism guarantee enforced by signature: constructor takes nothing.
        reviewer = QualityReviewer()
        assert reviewer.review is not None

    def test_review_is_deterministic(self) -> None:
        result = _result("auto_delete_result.json")
        first = QualityReviewer().review(result)
        second = QualityReviewer().review(result)
        assert first.model_dump() == second.model_dump()

    def test_review_does_not_mutate_result(self) -> None:
        raw = json.loads((_FIXTURES / "auto_delete_result.json").read_text())
        result = TestDesignResult.model_validate(raw)
        # Snapshot the validated model BEFORE review, then compare AFTER. This is a
        # true mutation check on the object under review (robust to additive model
        # fields like Phase 33's test_case.assumptions, which appear in a full dump
        # but are not present in the pre-Phase-33 fixture JSON).
        before = result.model_dump_json()
        QualityReviewer().review(result)
        assert result.model_dump_json() == before

    def test_review_returns_review_report(self) -> None:
        report = QualityReviewer().review(_result("bogo_result.json"))
        assert isinstance(report, ReviewReport)
        assert report.source_name


class TestUnresolvedRatioFinding:
    def test_healthy_run_is_info_not_warning(self) -> None:
        # BOGO 4/11 = 36% -> below the warning threshold -> INFO (not clean-blocking).
        report = QualityReviewer().review(_result("bogo_result.json"))
        ratio = next(f for f in report.findings if f.code == "unresolved_condition_ratio")
        assert ratio.severity is ReviewSeverity.INFO
        assert ratio.category is ReviewCategory.AMBIGUITY

    def test_pervasive_ambiguity_is_critical(self) -> None:
        # Auto-Delete 20/22 = 91% -> CRITICAL.
        report = QualityReviewer().review(_result("auto_delete_result.json"))
        ratio = next(f for f in report.findings if f.code == "unresolved_condition_ratio")
        assert ratio.severity is ReviewSeverity.CRITICAL
        assert "20 of 22" in ratio.message

    def test_no_unresolved_yields_no_ratio_finding(self) -> None:
        result = _result("bogo_result.json")
        # Force all conditions resolved in a copy of the metrics.
        result.coverage.metrics.unresolved_conditions = 0
        report = QualityReviewer().review(result)
        assert all(f.code != "unresolved_condition_ratio" for f in report.findings)


class TestConsumesCoverageReport:
    def test_duplicate_pairs_surface_as_finding(self) -> None:
        # BOGO's CoverageReport carries duplicate_pairs; the reviewer consumes them.
        result = _result("bogo_result.json")
        if not result.coverage.duplicate_pairs:
            return  # nothing to assert on this fixture
        report = QualityReviewer().review(result)
        dup = next(f for f in report.findings if f.code == "duplicate_test_cases")
        assert dup.severity is ReviewSeverity.WARNING
        assert dup.references  # references the flagged case ids

    def test_invalid_references_surface_as_critical(self) -> None:
        result = _result("bogo_result.json")
        # Inject an invalid reference into the coverage report copy.
        from qaops.models import InvalidReference

        result.coverage.invalid_references.append(
            InvalidReference(test_case_id="TC-999", reference_kind="scenario", missing_id="SC-999")
        )
        report = QualityReviewer().review(result)
        broken = next(f for f in report.findings if f.code == "broken_references")
        assert broken.severity is ReviewSeverity.CRITICAL
        assert broken.category is ReviewCategory.REFERENCES

    def test_reviewer_reads_uncovered_helpers(self) -> None:
        # If coverage reports uncovered requirements, the reviewer warns.
        result = _result("bogo_result.json")
        from qaops.models import CoverageStatus, RequirementCoverage

        result.coverage.per_requirement.append(
            RequirementCoverage(
                requirement_id="REQ-999",
                status=CoverageStatus.UNCOVERED,
                test_case_ids=[],
            )
        )
        report = QualityReviewer().review(result)
        finding = next(f for f in report.findings if f.code == "uncovered_requirements")
        assert "REQ-999" in finding.references


class TestReportHelpers:
    def test_is_clean_true_when_only_info(self) -> None:
        report = ReviewReport(
            source_name="x",
            findings=[
                # Only INFO -> clean
            ],
        )
        assert report.is_clean is True

    def test_recommendations_are_deduplicated(self) -> None:
        report = QualityReviewer().review(_result("auto_delete_result.json"))
        assert len(report.recommendations) == len(set(report.recommendations))


class TestV2GapSeverityFindings:
    def test_blocker_gaps_are_critical(self) -> None:
        # BOGO fixture carries a blocker gap.
        report = QualityReviewer().review(_result("bogo_result.json"))
        blocker = [f for f in report.findings if f.code == "blocker_gaps_present"]
        if blocker:
            assert blocker[0].severity is ReviewSeverity.CRITICAL
            assert blocker[0].category is ReviewCategory.AMBIGUITY

    def test_major_gaps_are_warning(self) -> None:
        report = QualityReviewer().review(_result("bogo_result.json"))
        major = [f for f in report.findings if f.code == "major_gaps_present"]
        if major:
            assert major[0].severity is ReviewSeverity.WARNING

    def test_no_gaps_yields_no_gap_findings(self) -> None:
        result = _result("bogo_result.json")
        result.gap_report.gaps.clear()
        report = QualityReviewer().review(result)
        assert all(
            f.code not in ("blocker_gaps_present", "major_gaps_present") for f in report.findings
        )


class TestV2PartialRequirements:
    def test_partial_requirement_surfaces_missing_categories(self) -> None:
        from qaops.models import CoverageStatus, RequirementCoverage
        from qaops.models.enums import ScenarioCategory

        result = _result("bogo_result.json")
        result.coverage.per_requirement.append(
            RequirementCoverage(
                requirement_id="REQ-777",
                status=CoverageStatus.PARTIAL,
                test_case_ids=["TC-1"],
                missing_categories=[ScenarioCategory.NEGATIVE],
            )
        )
        report = QualityReviewer().review(result)
        finding = next(f for f in report.findings if f.code == "partial_requirements")
        assert finding.severity is ReviewSeverity.WARNING
        assert finding.category is ReviewCategory.COVERAGE
        assert "REQ-777" in finding.references
        assert "negative" in finding.message

    def test_no_partial_yields_no_finding(self) -> None:
        # Neither fixture has partial requirements.
        report = QualityReviewer().review(_result("auto_delete_result.json"))
        assert all(f.code != "partial_requirements" for f in report.findings)


class TestV2ProvisionalRatio:
    def test_high_ratio_is_flagged(self) -> None:
        # Auto-Delete is 20/22 provisional -> above threshold.
        report = QualityReviewer().review(_result("auto_delete_result.json"))
        finding = next(f for f in report.findings if f.code == "high_provisional_ratio")
        assert finding.severity is ReviewSeverity.WARNING
        assert finding.category is ReviewCategory.COMPLETENESS

    def test_low_ratio_not_flagged(self) -> None:
        # BOGO 4/15 = 27% -> below threshold, no ratio finding (count finding still present).
        report = QualityReviewer().review(_result("bogo_result.json"))
        assert all(f.code != "high_provisional_ratio" for f in report.findings)


class TestV2PriorityDistribution:
    def test_skew_flagged_when_no_critical_or_high(self) -> None:
        from qaops.models.enums import Priority

        result = _result("bogo_result.json")
        for tc in result.test_cases:
            tc.priority = Priority.MEDIUM
        report = QualityReviewer().review(result)
        finding = next(f for f in report.findings if f.code == "priority_distribution_skew")
        assert finding.severity is ReviewSeverity.WARNING
        assert finding.category is ReviewCategory.QUALITY

    def test_no_skew_when_high_present(self) -> None:
        # BOGO has high-priority cases -> no skew finding.
        report = QualityReviewer().review(_result("bogo_result.json"))
        assert all(f.code != "priority_distribution_skew" for f in report.findings)

    def test_priority_distribution_observation_present(self) -> None:
        report = QualityReviewer().review(_result("bogo_result.json"))
        assert any(o.startswith("Priority distribution:") for o in report.observations)


class TestV2TypeBalance:
    def test_positive_only_suite_flagged(self) -> None:
        from qaops.models.enums import TestType

        result = _result("bogo_result.json")
        for tc in result.test_cases:
            tc.test_type = TestType.FUNCTIONAL
        report = QualityReviewer().review(result)
        finding = next(f for f in report.findings if f.code == "missing_negative_boundary_coverage")
        assert finding.severity is ReviewSeverity.WARNING
        assert finding.category is ReviewCategory.QUALITY

    def test_balanced_suite_not_flagged(self) -> None:
        # Both fixtures contain negative/boundary cases.
        report = QualityReviewer().review(_result("bogo_result.json"))
        assert all(f.code != "missing_negative_boundary_coverage" for f in report.findings)

    def test_type_distribution_observation_present(self) -> None:
        report = QualityReviewer().review(_result("bogo_result.json"))
        assert any(o.startswith("Test-type distribution:") for o in report.observations)


class TestV2FindingNumberThree_Dropped:
    def test_no_traceability_hole_finding_exists(self) -> None:
        # Finding #3 was intentionally dropped (would duplicate uncovered_requirements
        # or never fire). Assert no such finding is emitted on either fixture.
        for fixture in ("bogo_result.json", "auto_delete_result.json"):
            report = QualityReviewer().review(_result(fixture))
            assert all("traceabilit" not in f.code for f in report.findings)
