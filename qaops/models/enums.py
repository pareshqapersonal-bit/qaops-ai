"""Enumerations shared across QAOps domain models."""

from enum import StrEnum


class Priority(StrEnum):
    """Execution priority of a test case."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TestType(StrEnum):
    """Classification of a test case by intent."""

    FUNCTIONAL = "functional"
    NEGATIVE = "negative"
    BOUNDARY = "boundary"
    VALIDATION = "validation"
    PERMISSION = "permission"
    STATE_TRANSITION = "state_transition"
    INTEGRATION = "integration"
    UI = "ui"
    ERROR_HANDLING = "error_handling"


class ScenarioCategory(StrEnum):
    """QA design technique or focus area a scenario belongs to."""

    FUNCTIONAL = "functional"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    BOUNDARY_VALUE = "boundary_value"
    EQUIVALENCE_PARTITION = "equivalence_partition"
    INPUT_VALIDATION = "input_validation"
    ERROR_HANDLING = "error_handling"
    CRUD = "crud"
    PERMISSION = "permission"
    STATE_TRANSITION = "state_transition"
    INTEGRATION = "integration"
    UI = "ui"


class GapSeverity(StrEnum):
    """How strongly a requirement gap blocks confident test design."""

    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"


class CoverageStatus(StrEnum):
    """Coverage verdict for a single requirement."""

    COVERED = "covered"
    PARTIAL = "partial"
    UNCOVERED = "uncovered"


class ConditionCategory(StrEnum):
    """Test-design technique / focus a test condition applies (ADR-036).

    Aligned with ScenarioCategory/TestType vocabulary so the three layers
    share one taxonomy. A condition carries the technique that produced it,
    which the coverage report groups by.
    """

    POSITIVE = "positive"
    NEGATIVE = "negative"
    BOUNDARY = "boundary"
    EQUIVALENCE = "equivalence"
    VALIDATION = "validation"
    ELIGIBILITY = "eligibility"
    STATE_TRANSITION = "state_transition"
    ALTERNATE_FLOW = "alternate_flow"
    ERROR_HANDLING = "error_handling"
    BUSINESS_RULE = "business_rule"
    DATA_VARIATION = "data_variation"
    ROLE_VARIATION = "role_variation"
    COMBINATION = "combination"


class SourceBasis(StrEnum):
    """The kind of evidence that justifies a test condition (ADR-036).

    Every condition must cite one, so a reader can answer 'why does this
    test exist?'. Derived bases (boundary/equivalence/state/combination)
    require the underlying documented rule/limit to be present; otherwise
    the condition is unsupported and must not be generated.
    """

    EXPLICIT_REQUIREMENT = "explicit_requirement"
    EXPLICIT_RULE = "explicit_rule"
    SCENARIO = "scenario"
    DERIVED_BOUNDARY = "derived_boundary"
    DERIVED_EQUIVALENCE = "derived_equivalence"
    DOCUMENTED_COMBINATION = "documented_combination"
    DOCUMENTED_STATE_TRANSITION = "documented_state_transition"


class ConditionStatus(StrEnum):
    """Whether a condition's expected behaviour is established (ADR-036).

    RESOLVED conditions yield ordinary executable test cases. UNRESOLVED
    conditions have meaningful test intent but undocumented expected
    behaviour: they are preserved, linked to a gap, and never counted as
    fully covered, and their test cases (if any) are provisional.
    """

    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


class ReviewSeverity(StrEnum):
    """Severity of a quality-review finding (ADR-045, Phase 30).

    Advisory only: severity communicates how much attention a finding warrants,
    never whether the run succeeded. A run with CRITICAL review findings is still
    a COMPLETED run - the QualityReviewer never gates execution.
    """

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ReviewCategory(StrEnum):
    """Category of a quality-review finding (ADR-045, Phase 30)."""

    COVERAGE = "coverage"
    AMBIGUITY = "ambiguity"
    DUPLICATION = "duplication"
    REFERENCES = "references"
    COMPLETENESS = "completeness"
    # Phase 30 v2 (ADR-045): QA-balance signals over the generated suite -
    # priority distribution and test-type/category coverage. Additive.
    QUALITY = "quality"
