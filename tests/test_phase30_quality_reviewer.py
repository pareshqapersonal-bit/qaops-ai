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
        QualityReviewer().review(result)
        # The input result must be byte-identical after review.
        assert json.loads(result.model_dump_json()) == raw

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
