"""Phase 29 tests: evidence-first unresolved classification + justification gate.

Phase 29 reduces false-positive "unresolved" classifications. Two additive
changes are covered here:

  * a deterministic, report-only validation gate that detects unresolved
    conditions lacking a substantive gap-backed justification; and
  * the guarantee that the gate NEVER reclassifies - the model remains
    responsible for the classification, and genuinely unsupported behaviour
    stays unresolved.

The prompt strengthening (evidence-first) is behavioural and validated through
the existing fixture/LLM tests; here we pin the deterministic pieces.
"""

from qaops.models import TestCondition
from qaops.models.enums import ConditionCategory, ConditionStatus, SourceBasis
from qaops.pipelines.test_design.conditions import TestConditionAnalyzer


def _cond(
    cid: str,
    status: ConditionStatus,
    gap_reference: str = "",
) -> TestCondition:
    return TestCondition(
        id=cid,
        scenario_id="SC-001",
        requirement_ids=["REQ-001"],
        business_rule_ids=[],
        category=ConditionCategory.POSITIVE,
        description="A condition.",
        rationale="because.",
        source_basis=SourceBasis.EXPLICIT_REQUIREMENT,
        status=status,
        parameters={},
        gap_reference=gap_reference,
    )


class TestSubstantiveJustification:
    def test_empty_is_not_substantive(self) -> None:
        assert TestConditionAnalyzer._is_substantive_justification("") is False

    def test_bare_confirm_phrases_are_not_substantive(self) -> None:
        for phrase in (
            "confirm with the PO",
            "confirm with PO",
            "confirm with the product owner",
            "confirm with the client",
            "TBD",
            "unknown",
            "n/a",
        ):
            assert TestConditionAnalyzer._is_substantive_justification(phrase) is False, phrase

    def test_too_short_is_not_substantive(self) -> None:
        assert TestConditionAnalyzer._is_substantive_justification("unclear") is False

    def test_specific_open_question_is_substantive(self) -> None:
        ref = "The exact tag copy for the second offer is unspecified."
        assert TestConditionAnalyzer._is_substantive_justification(ref) is True

    def test_confirm_phrase_with_real_question_is_substantive(self) -> None:
        # A confirm phrase that ALSO states what is missing is fine - it is not a
        # bare placeholder.
        ref = "Confirm with the PO whether the lockout duration is 15 or 30 minutes."
        assert TestConditionAnalyzer._is_substantive_justification(ref) is True


class TestReportUnjustifiedUnresolved:
    def _analyzer(self) -> TestConditionAnalyzer:
        # The reporter is a pure method over its arguments; construct without
        # running the stage.
        return TestConditionAnalyzer.__new__(TestConditionAnalyzer)

    def test_resolved_conditions_are_never_flagged(self) -> None:
        analyzer = self._analyzer()
        conds = [
            _cond("COND-001", ConditionStatus.RESOLVED),
            _cond("COND-002", ConditionStatus.RESOLVED, gap_reference=""),
        ]
        assert analyzer._report_unjustified_unresolved(conds, []) == []

    def test_unresolved_with_substantive_reference_is_justified(self) -> None:
        analyzer = self._analyzer()
        conds = [
            _cond(
                "COND-001",
                ConditionStatus.UNRESOLVED,
                gap_reference="The exact tag copy for the second offer is unspecified.",
            )
        ]
        assert analyzer._report_unjustified_unresolved(conds, []) == []

    def test_unresolved_without_reference_is_flagged(self) -> None:
        analyzer = self._analyzer()
        conds = [_cond("COND-001", ConditionStatus.UNRESOLVED, gap_reference="")]
        assert analyzer._report_unjustified_unresolved(conds, []) == ["COND-001"]

    def test_unresolved_with_bare_confirm_is_flagged(self) -> None:
        analyzer = self._analyzer()
        conds = [_cond("COND-001", ConditionStatus.UNRESOLVED, gap_reference="Confirm with the PO")]
        assert analyzer._report_unjustified_unresolved(conds, []) == ["COND-001"]

    def test_report_does_not_mutate_conditions(self) -> None:
        # The gate is report-only: it must not reclassify or alter any condition.
        analyzer = self._analyzer()
        conds = [_cond("COND-001", ConditionStatus.UNRESOLVED, gap_reference="")]
        before = [c.model_dump() for c in conds]
        analyzer._report_unjustified_unresolved(conds, [])
        after = [c.model_dump() for c in conds]
        assert before == after
        assert conds[0].status is ConditionStatus.UNRESOLVED  # still unresolved

    def test_mixed_batch_flags_only_unjustified(self) -> None:
        analyzer = self._analyzer()
        conds = [
            _cond("COND-001", ConditionStatus.RESOLVED),
            _cond(
                "COND-002",
                ConditionStatus.UNRESOLVED,
                gap_reference="The retry limit for failed logins is not stated.",
            ),
            _cond("COND-003", ConditionStatus.UNRESOLVED, gap_reference="TBD"),
        ]
        assert analyzer._report_unjustified_unresolved(conds, []) == ["COND-003"]
