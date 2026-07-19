"""Typed domain models exchanged between pipeline stages.

Every stage consumes and produces these models. Raw dicts and raw JSON
never cross a stage boundary. IDs (REQ-*, BR-*, SC-*, TC-*) are assigned
deterministically by code, never by the LLM, so traceability and
coverage validation remain trustworthy.
"""

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from qaops.models.enums import (
    CoverageStatus,
    GapSeverity,
    Priority,
    ScenarioCategory,
    TestType,
)

_ID_PATTERNS: dict[str, re.Pattern[str]] = {
    "REQ": re.compile(r"^REQ-\d{3,}$"),
    "BR": re.compile(r"^BR-\d{3,}$"),
    "SC": re.compile(r"^SC-\d{3,}$"),
    "TC": re.compile(r"^TC-\d{3,}$"),
}

NonEmptyStr = Annotated[str, Field(min_length=1)]


class _StrictModel(BaseModel):
    """Base model: no unknown fields, values validated on assignment."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


def _validate_prefixed_id(value: str, prefix: str) -> str:
    pattern = _ID_PATTERNS[prefix]
    if not pattern.match(value):
        msg = f"Invalid id {value!r}: expected pattern {prefix}-NNN (e.g. {prefix}-001)"
        raise ValueError(msg)
    return value


class RequirementInput(_StrictModel):
    """Raw requirement text entering the pipeline.

    V1 accepts plain text and Markdown only. File parsing (docx/pdf)
    is a future input stage, not a model concern.
    """

    text: NonEmptyStr
    source_name: str = "inline"


class Requirement(_StrictModel):
    """A single structured requirement extracted from the input."""

    id: NonEmptyStr
    title: NonEmptyStr
    description: NonEmptyStr
    source_excerpt: str = Field(
        default="",
        description="Verbatim excerpt from the input that grounds this requirement.",
    )
    actors: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    validations: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return _validate_prefixed_id(value, "REQ")


class BusinessRule(_StrictModel):
    """A discrete business rule tied to a requirement."""

    id: NonEmptyStr
    requirement_id: NonEmptyStr
    rule: NonEmptyStr
    source_excerpt: str = ""

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return _validate_prefixed_id(value, "BR")

    @field_validator("requirement_id")
    @classmethod
    def _check_req_id(cls, value: str) -> str:
        return _validate_prefixed_id(value, "REQ")


class Gap(_StrictModel):
    """A single ambiguity or missing detail found in the requirements."""

    description: NonEmptyStr
    severity: GapSeverity = GapSeverity.MAJOR
    requirement_id: str | None = None
    suggested_question: str = Field(
        default="",
        description="The question a QA engineer would ask the BA/PO to close this gap.",
    )


class GapReport(_StrictModel):
    """Ambiguity and gap analysis produced before test design begins."""

    gaps: list[Gap] = Field(default_factory=list)

    @property
    def has_blockers(self) -> bool:
        return any(g.severity is GapSeverity.BLOCKER for g in self.gaps)


class Scenario(_StrictModel):
    """A test scenario derived from one or more requirements."""

    id: NonEmptyStr
    title: NonEmptyStr
    description: str = ""
    category: ScenarioCategory
    requirement_ids: list[str] = Field(min_length=1)

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return _validate_prefixed_id(value, "SC")

    @field_validator("requirement_ids")
    @classmethod
    def _check_req_ids(cls, values: list[str]) -> list[str]:
        return [_validate_prefixed_id(v, "REQ") for v in values]


class TestStep(_StrictModel):
    """A single numbered step within a test case."""

    # Domain class, not a pytest test class, despite the Test* name.
    __test__ = False

    number: int = Field(ge=1)
    action: NonEmptyStr
    expected: str = ""


class TestCase(_StrictModel):
    """A production-quality manual test case."""

    # Domain class, not a pytest test class, despite the Test* name.
    __test__ = False

    id: NonEmptyStr
    scenario_id: NonEmptyStr
    requirement_ids: list[str] = Field(min_length=1)
    module: str = ""
    feature: str = ""
    title: NonEmptyStr
    objective: str = ""
    preconditions: list[str] = Field(default_factory=list)
    test_data: dict[str, str] = Field(default_factory=dict)
    steps: list[TestStep] = Field(min_length=1)
    expected_result: NonEmptyStr
    priority: Priority = Priority.MEDIUM
    test_type: TestType = TestType.FUNCTIONAL
    tags: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return _validate_prefixed_id(value, "TC")

    @field_validator("scenario_id")
    @classmethod
    def _check_sc_id(cls, value: str) -> str:
        return _validate_prefixed_id(value, "SC")

    @field_validator("requirement_ids")
    @classmethod
    def _check_req_ids(cls, values: list[str]) -> list[str]:
        return [_validate_prefixed_id(v, "REQ") for v in values]

    @field_validator("steps")
    @classmethod
    def _check_step_order(cls, steps: list[TestStep]) -> list[TestStep]:
        numbers = [s.number for s in steps]
        if numbers != list(range(1, len(steps) + 1)):
            msg = f"Test steps must be numbered 1..N without gaps, got {numbers}"
            raise ValueError(msg)
        return steps


class RequirementCoverage(_StrictModel):
    """Coverage verdict for one requirement, computed by code."""

    requirement_id: NonEmptyStr
    status: CoverageStatus
    test_case_ids: list[str] = Field(default_factory=list)
    missing_categories: list[ScenarioCategory] = Field(default_factory=list)


class TraceabilityMatrix(_StrictModel):
    """Requirement -> test case mapping, computed deterministically."""

    entries: dict[str, list[str]] = Field(default_factory=dict)


class CoverageReport(_StrictModel):
    """Full coverage validation output. Pure code, zero LLM calls."""

    per_requirement: list[RequirementCoverage] = Field(default_factory=list)
    traceability: TraceabilityMatrix = Field(default_factory=TraceabilityMatrix)
    suspected_duplicates: list[tuple[str, str]] = Field(
        default_factory=list,
        description="Pairs of test case IDs flagged as likely duplicates.",
    )

    @property
    def uncovered_requirement_ids(self) -> list[str]:
        return [
            rc.requirement_id
            for rc in self.per_requirement
            if rc.status is CoverageStatus.UNCOVERED
        ]


class RequirementAnalysisResult(_StrictModel):
    """Aggregate output of Phase 2's requirement-analysis pipeline.

    Progressively enriched: RequirementAnalyzer fills requirements,
    BusinessRuleExtractor adds business_rules, GapAnalyzer adds
    gap_report. source_text is retained so downstream stages can ground
    their analysis in the original wording.
    """

    source_name: str
    source_text: NonEmptyStr
    requirements: list[Requirement] = Field(default_factory=list)
    business_rules: list[BusinessRule] = Field(default_factory=list)
    gap_report: GapReport = Field(default_factory=GapReport)


class TestDesignResult(_StrictModel):
    """Aggregate output of the full Test Design pipeline run."""

    # Domain class, not a pytest test class, despite the Test* name.
    __test__ = False

    source_name: str
    requirements: list[Requirement] = Field(default_factory=list)
    business_rules: list[BusinessRule] = Field(default_factory=list)
    gap_report: GapReport = Field(default_factory=GapReport)
    scenarios: list[Scenario] = Field(default_factory=list)
    test_cases: list[TestCase] = Field(default_factory=list)
    coverage: CoverageReport = Field(default_factory=CoverageReport)
