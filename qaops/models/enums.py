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
