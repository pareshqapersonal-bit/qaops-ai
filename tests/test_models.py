"""Domain model tests: validation rules, ID patterns, invariants."""

import pytest
from pydantic import ValidationError

from qaops.models import (
    CoverageReport,
    CoverageStatus,
    Gap,
    GapReport,
    GapSeverity,
    Requirement,
    RequirementCoverage,
    RequirementInput,
    Scenario,
    ScenarioCategory,
    TestCase,
    TestStep,
)


def make_test_case(**overrides: object) -> TestCase:
    base: dict[str, object] = {
        "id": "TC-001",
        "scenario_id": "SC-001",
        "requirement_ids": ["REQ-001"],
        "title": "Login with valid credentials",
        "steps": [
            TestStep(number=1, action="Open login page"),
            TestStep(number=2, action="Enter valid credentials", expected="Fields accept input"),
            TestStep(number=3, action="Click Login"),
        ],
        "expected_result": "User lands on the dashboard",
    }
    base.update(overrides)
    return TestCase.model_validate(base)


class TestRequirementInput:
    def test_accepts_plain_text(self) -> None:
        ri = RequirementInput(text="Users can reset their password via email.")
        assert ri.source_name == "inline"

    def test_rejects_empty_text(self) -> None:
        with pytest.raises(ValidationError):
            RequirementInput(text="")


class TestRequirement:
    def test_valid_requirement(self) -> None:
        req = Requirement(
            id="REQ-001",
            title="Password reset",
            description="Users can reset their password via an email link.",
            actors=["Registered user"],
        )
        assert req.id == "REQ-001"

    @pytest.mark.parametrize("bad_id", ["REQ1", "req-001", "REQ-1", "TC-001", ""])
    def test_rejects_malformed_ids(self, bad_id: str) -> None:
        with pytest.raises(ValidationError):
            Requirement(id=bad_id, title="t", description="d")

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            Requirement.model_validate(
                {"id": "REQ-001", "title": "t", "description": "d", "hallucinated": True}
            )


class TestScenario:
    def test_requires_at_least_one_requirement_link(self) -> None:
        with pytest.raises(ValidationError):
            Scenario(
                id="SC-001",
                title="Boundary check",
                category=ScenarioCategory.BOUNDARY_VALUE,
                requirement_ids=[],
            )

    def test_validates_linked_requirement_ids(self) -> None:
        with pytest.raises(ValidationError):
            Scenario(
                id="SC-001",
                title="Boundary check",
                category=ScenarioCategory.BOUNDARY_VALUE,
                requirement_ids=["REQ-XYZ"],
            )


class TestTestCase:
    def test_valid_test_case(self) -> None:
        tc = make_test_case()
        assert tc.priority.value == "medium"
        assert len(tc.steps) == 3

    def test_steps_must_be_sequential_from_one(self) -> None:
        with pytest.raises(ValidationError, match="numbered 1..N"):
            make_test_case(
                steps=[
                    TestStep(number=1, action="Open page"),
                    TestStep(number=3, action="Skip a step"),
                ]
            )

    def test_requires_at_least_one_step(self) -> None:
        with pytest.raises(ValidationError):
            make_test_case(steps=[])

    def test_round_trips_through_json(self) -> None:
        tc = make_test_case()
        restored = TestCase.model_validate_json(tc.model_dump_json())
        assert restored == tc


class TestGapReport:
    def test_has_blockers(self) -> None:
        report = GapReport(
            gaps=[
                Gap(description="Password length not specified", severity=GapSeverity.MINOR),
                Gap(
                    description="No behavior defined for expired token",
                    severity=GapSeverity.BLOCKER,
                ),
            ]
        )
        assert report.has_blockers

    def test_empty_report_has_no_blockers(self) -> None:
        assert not GapReport().has_blockers


class TestCoverageReport:
    def test_uncovered_requirement_ids(self) -> None:
        report = CoverageReport(
            per_requirement=[
                RequirementCoverage(
                    requirement_id="REQ-001",
                    status=CoverageStatus.COVERED,
                    test_case_ids=["TC-001"],
                ),
                RequirementCoverage(requirement_id="REQ-002", status=CoverageStatus.UNCOVERED),
            ]
        )
        assert report.uncovered_requirement_ids == ["REQ-002"]
