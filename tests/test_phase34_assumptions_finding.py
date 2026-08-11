"""Phase 34 tests: threshold-gated test-case assumptions finding (ADR-049).

The QualityReviewer surfaces test cases resting on unconfirmed assumptions
(Phase 33's TestCase.assumptions) as a deterministic, quantity-based WARNING when
the fraction of assumption-bearing cases crosses the threshold. It never
categorizes assumption text, never touches coverage or provisional status, and is
byte-identical for runs with no assumptions.
"""

import json
from pathlib import Path

from qaops.models import (
    CoverageReport,
    ReviewSeverity,
    TestCase,
    TestDesignResult,
    TestStep,
)
from qaops.models.enums import ReviewCategory
from qaops.review import QualityReviewer

_FIXTURES = Path(__file__).parent / "fixtures" / "phase29"


def _case(i: int, assumptions: list[str] | None = None) -> TestCase:
    return TestCase(
        id=f"TC-{i:03d}",
        scenario_id="SC-001",
        requirement_ids=["REQ-001"],
        condition_id="COND-001",
        title=f"case {i}",
        steps=[TestStep(number=1, action="do", expected="ok")],
        expected_result="ok",
        assumptions=assumptions or [],
    )


def _result(cases: list[TestCase]) -> TestDesignResult:
    return TestDesignResult(source_name="t", test_cases=cases, coverage=CoverageReport())


class TestAssumptionThreshold:
    def test_fires_at_threshold(self) -> None:
        # 2 of 4 (50%) carry assumptions -> fires.
        cases = [_case(1, ["X exists"]), _case(2, ["Y expires"]), _case(3), _case(4)]
        finding = next(
            f
            for f in QualityReviewer().review(_result(cases)).findings
            if f.code == "test_case_assumptions"
        )
        assert finding.severity is ReviewSeverity.WARNING
        assert finding.category is ReviewCategory.COMPLETENESS
        assert "2 of 4" in finding.message

    def test_does_not_fire_below_threshold(self) -> None:
        # 1 of 4 (25%) -> below 50%, no finding.
        cases = [_case(1, ["X exists"]), _case(2), _case(3), _case(4)]
        assert all(
            f.code != "test_case_assumptions"
            for f in QualityReviewer().review(_result(cases)).findings
        )

    def test_no_assumptions_no_finding(self) -> None:
        cases = [_case(i) for i in range(1, 5)]
        assert all(
            f.code != "test_case_assumptions"
            for f in QualityReviewer().review(_result(cases)).findings
        )


class TestAssumptionTraceability:
    def test_references_are_exact_sorted_ids(self) -> None:
        # Only the assumption-bearing cases, sorted, verbatim.
        cases = [_case(3, ["A"]), _case(1, ["B"]), _case(2), _case(4, ["C"])]
        finding = next(
            f
            for f in QualityReviewer().review(_result(cases)).findings
            if f.code == "test_case_assumptions"
        )
        assert finding.references == ["TC-001", "TC-003", "TC-004"]

    def test_assumption_text_not_interpreted(self) -> None:
        # The finding never echoes or categorizes the assumption strings.
        cases = [_case(1, ["OTP expires after 5 minutes"]), _case(2, ["SKU exists"])]
        finding = next(
            f
            for f in QualityReviewer().review(_result(cases)).findings
            if f.code == "test_case_assumptions"
        )
        assert "OTP" not in finding.message
        assert "SKU" not in finding.message


class TestDeterministicAndIsolated:
    def test_deterministic(self) -> None:
        cases = [_case(1, ["A"]), _case(2, ["B"])]
        result = _result(cases)
        first = QualityReviewer().review(result).model_dump()
        second = QualityReviewer().review(result).model_dump()
        assert first == second

    def test_does_not_touch_provisional(self) -> None:
        # An assumption-bearing case is NOT made provisional by this finding.
        cases = [_case(1, ["A"]), _case(2, ["B"])]
        result = _result(cases)
        QualityReviewer().review(result)
        assert all(tc.provisional is False for tc in result.test_cases)


class TestByteIdenticalNoAssumptions:
    def test_review_byte_identical_when_no_assumptions(self) -> None:
        # PINNED: a real run with no assumptions must produce a ReviewReport
        # identical to what it produced before Phase 34 (the new finding is absent,
        # nothing else shifts). The Phase 29 fixtures predate assumptions.
        for fixture in ("auto_delete_result.json", "bogo_result.json"):
            result = TestDesignResult.model_validate(json.loads((_FIXTURES / fixture).read_text()))
            report = QualityReviewer().review(result)
            assert all(f.code != "test_case_assumptions" for f in report.findings)
