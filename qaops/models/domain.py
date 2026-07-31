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
    ConditionCategory,
    ConditionStatus,
    CoverageStatus,
    GapSeverity,
    Priority,
    ScenarioCategory,
    SourceBasis,
    TestType,
)

_ID_PATTERNS: dict[str, re.Pattern[str]] = {
    "REQ": re.compile(r"^REQ-\d{3,}$"),
    "BR": re.compile(r"^BR-\d{3,}$"),
    "SC": re.compile(r"^SC-\d{3,}$"),
    "COND": re.compile(r"^COND-\d{3,}$"),
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


class TestCondition(_StrictModel):
    """A distinct testable proposition derived from evidence (ADR-036).

    Sits between Scenario and TestCase: one scenario may yield one or many
    conditions, and one condition may yield one or more test cases when data
    variants, boundaries, or states genuinely require distinct execution.
    Every condition cites evidence (its source_basis plus the referenced
    requirement/rule/scenario IDs) so a reader can answer 'why does this test
    exist?'. IDs are assigned by code (COND-*), never by the model.

    When expected behaviour cannot be established from the evidence, the
    condition is preserved with status=UNRESOLVED and linked to a gap
    (gap_reference) instead of inventing an expected result.
    """

    # Domain class, not a pytest test class, despite the Test* name.
    __test__ = False

    id: NonEmptyStr
    scenario_id: NonEmptyStr
    requirement_ids: list[str] = Field(default_factory=list)
    business_rule_ids: list[str] = Field(default_factory=list)
    category: ConditionCategory
    description: NonEmptyStr
    rationale: str = ""
    source_basis: SourceBasis
    status: ConditionStatus = ConditionStatus.RESOLVED
    # Dimension -> value used for combinatorial control and dedup signatures
    # (e.g. {"quantity": "2", "eligibility": "eligible"}).
    parameters: dict[str, str] = Field(default_factory=dict)
    # When unresolved, the GAP-/question text this condition raised, so the
    # ambiguity is traceable and not silently dropped.
    gap_reference: str = ""

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return _validate_prefixed_id(value, "COND")

    @field_validator("scenario_id")
    @classmethod
    def _check_scenario_id(cls, value: str) -> str:
        return _validate_prefixed_id(value, "SC")

    @field_validator("requirement_ids")
    @classmethod
    def _check_req_ids(cls, values: list[str]) -> list[str]:
        return [_validate_prefixed_id(v, "REQ") for v in values]

    @field_validator("business_rule_ids")
    @classmethod
    def _check_rule_ids(cls, values: list[str]) -> list[str]:
        return [_validate_prefixed_id(v, "BR") for v in values]


class TestCase(_StrictModel):
    """A production-quality manual test case."""

    # Domain class, not a pytest test class, despite the Test* name.
    __test__ = False

    id: NonEmptyStr
    scenario_id: NonEmptyStr
    requirement_ids: list[str] = Field(min_length=1)
    # Upstream condition this case validates (ADR-036). Optional so artifacts
    # generated before Phase 21 remain valid; new runs always populate it.
    condition_id: str | None = None
    # True when the case validates an UNRESOLVED condition: it exercises the
    # documented steps but its expected behaviour is not established by
    # evidence, so it must not be presented as a normal passing assertion.
    provisional: bool = False
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


class BusinessRuleCoverage(_StrictModel):
    """Coverage verdict for one business rule, computed transitively.

    A rule has no direct test-case link; it is covered when a test case
    covers the requirement the rule belongs to. test_case_ids lists the
    cases that reach the rule's requirement.
    """

    rule_id: NonEmptyStr
    requirement_id: NonEmptyStr
    status: CoverageStatus
    test_case_ids: list[str] = Field(default_factory=list)


class ScenarioCoverage(_StrictModel):
    """Coverage verdict for one scenario, computed by code."""

    scenario_id: NonEmptyStr
    status: CoverageStatus
    test_case_ids: list[str] = Field(default_factory=list)


class ConditionCoverage(_StrictModel):
    """Coverage verdict for one test condition, computed by code (ADR-036).

    COVERED when a RESOLVED condition has >=1 non-provisional test case;
    UNCOVERED when a resolved condition has none. An UNRESOLVED condition is
    reported with status UNCOVERED and unresolved=True so it is visible and
    never silently treated as done.
    """

    condition_id: NonEmptyStr
    scenario_id: NonEmptyStr
    status: CoverageStatus
    unresolved: bool = False
    test_case_ids: list[str] = Field(default_factory=list)


class DuplicatePair(_StrictModel):
    """Two test cases flagged as suspected near-duplicates, with the reason."""

    test_case_id_a: NonEmptyStr
    test_case_id_b: NonEmptyStr
    reason: str = ""


class InvalidReference(_StrictModel):
    """A test-case reference pointing at an ID absent from the result.

    Prior stages reject these at generation, so at the validation layer
    this list should always be empty; a non-empty list is a defect
    report, not a normal outcome.
    """

    test_case_id: NonEmptyStr
    reference_kind: str  # "scenario" or "requirement"
    missing_id: NonEmptyStr


class CoverageMetrics(_StrictModel):
    """Aggregate coverage percentages, computed by code.

    Percentages are 0.0-100.0, rounded to one decimal. A denominator of
    zero yields 0.0 (nothing to cover is reported as 0% covered, never a
    division error).
    """

    total_requirements: int = 0
    covered_requirements: int = 0
    total_business_rules: int = 0
    covered_business_rules: int = 0
    total_scenarios: int = 0
    covered_scenarios: int = 0
    total_test_cases: int = 0
    # Phase 21 (ADR-036): condition-level depth. total_conditions counts all
    # identified conditions; covered_conditions counts RESOLVED conditions that
    # have >=1 non-provisional test case. Unresolved conditions are counted in
    # the total but never as covered, so condition coverage cannot be inflated
    # by ambiguity. Defaults keep pre-Phase-21 construction valid.
    total_conditions: int = 0
    covered_conditions: int = 0
    unresolved_conditions: int = 0
    # True when a configured expansion bound stopped generation while more
    # valid candidates may remain: condition coverage is then NOT exhaustive
    # and the UI/report must say so.
    expansion_truncated: bool = False

    @staticmethod
    def _pct(covered: int, total: int) -> float:
        return round(100.0 * covered / total, 1) if total else 0.0

    @property
    def requirement_coverage_pct(self) -> float:
        return self._pct(self.covered_requirements, self.total_requirements)

    @property
    def business_rule_coverage_pct(self) -> float:
        return self._pct(self.covered_business_rules, self.total_business_rules)

    @property
    def scenario_coverage_pct(self) -> float:
        return self._pct(self.covered_scenarios, self.total_scenarios)

    @property
    def condition_coverage_pct(self) -> float:
        """Fraction of identified conditions realized by a real test case.

        Denominator is all identified conditions (resolved + unresolved), so
        unresolved conditions lower this metric rather than being hidden. When
        expansion was truncated this figure is a floor, not an exhaustive claim.
        """
        return self._pct(self.covered_conditions, self.total_conditions)


class TraceabilityMatrix(_StrictModel):
    """Requirement -> test case mapping, computed deterministically."""

    entries: dict[str, list[str]] = Field(default_factory=dict)


class CoverageReport(_StrictModel):
    """Full coverage validation output. Pure code, zero LLM calls.

    Extended in Phase 5 with business-rule and scenario coverage,
    aggregate metrics, structured duplicate pairs, and invalid-reference
    reporting. All additions are optional with defaults, so the Phase 0
    construction (`CoverageReport()`) and the Phase 4 pass-through remain
    valid; `suspected_duplicates` is retained for backward compatibility.
    """

    per_requirement: list[RequirementCoverage] = Field(default_factory=list)
    per_business_rule: list[BusinessRuleCoverage] = Field(default_factory=list)
    per_scenario: list[ScenarioCoverage] = Field(default_factory=list)
    per_condition: list[ConditionCoverage] = Field(default_factory=list)
    traceability: TraceabilityMatrix = Field(default_factory=TraceabilityMatrix)
    metrics: CoverageMetrics = Field(default_factory=CoverageMetrics)
    duplicate_pairs: list[DuplicatePair] = Field(default_factory=list)
    invalid_references: list[InvalidReference] = Field(default_factory=list)
    suspected_duplicates: list[tuple[str, str]] = Field(
        default_factory=list,
        description="Backward-compatible flat pairs of test case IDs flagged as "
        "likely duplicates. Mirrors duplicate_pairs.",
    )

    @property
    def uncovered_requirement_ids(self) -> list[str]:
        return [
            rc.requirement_id
            for rc in self.per_requirement
            if rc.status is CoverageStatus.UNCOVERED
        ]

    @property
    def uncovered_business_rule_ids(self) -> list[str]:
        return [
            bc.rule_id for bc in self.per_business_rule if bc.status is CoverageStatus.UNCOVERED
        ]

    @property
    def uncovered_scenario_ids(self) -> list[str]:
        return [sc.scenario_id for sc in self.per_scenario if sc.status is CoverageStatus.UNCOVERED]

    @property
    def has_invalid_references(self) -> bool:
        return bool(self.invalid_references)


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


class ScenarioDesignResult(_StrictModel):
    """Aggregate output of Phase 3: the analysis plus generated scenarios.

    Composes rather than copies the analysis result, so Phase 2's output
    stays immutable and the provenance of every scenario's requirement
    references is auditable.
    """

    analysis: RequirementAnalysisResult
    scenarios: list[Scenario] = Field(default_factory=list)


class ConditionDesignResult(_StrictModel):
    """Scenario design plus derived test conditions (ADR-036).

    The carrier between TestConditionAnalyzer and TestCaseGenerator. Composes
    the scenario design so scenarios/analysis stay immutable, and adds the
    conditions plus a flag recording whether an expansion bound truncated
    condition generation.
    """

    scenario_design: ScenarioDesignResult
    conditions: list[TestCondition] = Field(default_factory=list)
    expansion_truncated: bool = False
    truncation_note: str = ""


class TestDesignResult(_StrictModel):
    """Aggregate output of the full Test Design pipeline run."""

    # Domain class, not a pytest test class, despite the Test* name.
    __test__ = False

    source_name: str
    requirements: list[Requirement] = Field(default_factory=list)
    business_rules: list[BusinessRule] = Field(default_factory=list)
    gap_report: GapReport = Field(default_factory=GapReport)
    scenarios: list[Scenario] = Field(default_factory=list)
    conditions: list[TestCondition] = Field(default_factory=list)
    test_cases: list[TestCase] = Field(default_factory=list)
    coverage: CoverageReport = Field(default_factory=CoverageReport)
    # Set when a Phase 21 expansion bound truncated generation, so coverage
    # is reported as non-exhaustive.
    expansion_truncated: bool = False
    truncation_note: str = ""
