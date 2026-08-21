"""Phase 41E-2 tests: pure gap-diff + duplicate-prevention layer.

Covers the deterministic signature (normalization, requirement-id distinctness,
None/"null" non-collision, no semantic matching) and the NEW/PERSISTING/RESOLVED/
ACCEPTED classification, plus purity (no input mutation, deterministic repeats).
No LLM, no I/O, no service wiring.
"""

from qaops.clarification.gap_diff import (
    GapClassification,
    diff_gaps,
    gap_signature,
    gap_signature_for,
    normalize_gap_description,
)
from qaops.clarification.models import ClarificationState
from qaops.models.domain import Gap, GapReport
from qaops.models.enums import GapSeverity


def _gap(
    description: str,
    requirement_id: str | None = "REQ-001",
    severity: GapSeverity = GapSeverity.BLOCKER,
) -> Gap:
    return Gap(
        description=description,
        severity=severity,
        requirement_id=requirement_id,
        suggested_question="What is the expected behaviour?",
    )


class TestSignature:
    def test_identical_gap_same_signature(self) -> None:
        assert gap_signature("REQ-1", "retry is undefined") == gap_signature(
            "REQ-1", "retry is undefined"
        )

    def test_whitespace_normalization(self) -> None:
        assert gap_signature("REQ-1", "  retry   is    undefined  ") == gap_signature(
            "REQ-1", "retry is undefined"
        )

    def test_case_normalization(self) -> None:
        assert gap_signature("REQ-1", "Retry Is UNDEFINED") == gap_signature(
            "REQ-1", "retry is undefined"
        )

    def test_different_requirement_ids_differ(self) -> None:
        assert gap_signature("REQ-1", "retry undefined") != gap_signature(
            "REQ-2", "retry undefined"
        )

    def test_none_requirement_id_distinct_from_real_id(self) -> None:
        assert gap_signature(None, "retry undefined") != gap_signature("REQ-1", "retry undefined")

    def test_none_does_not_collide_with_literal_null_or_none(self) -> None:
        # The literal strings "null" / "None" as requirement ids must not collide
        # with a genuinely missing (None) requirement id.
        assert gap_signature(None, "x") != gap_signature("null", "x")
        assert gap_signature(None, "x") != gap_signature("None", "x")
        assert gap_signature(None, "x") != gap_signature("", "x")

    def test_no_semantic_matching(self) -> None:
        # Two materially different descriptions that merely sound related must stay
        # separate - the layer is lexical only, never semantic.
        assert gap_signature("REQ-1", "retry policy is undefined") != gap_signature(
            "REQ-1", "timeout policy is undefined"
        )
        assert gap_signature("REQ-1", "user can log in") != gap_signature(
            "REQ-1", "user cannot log in"
        )

    def test_signature_excludes_severity(self) -> None:
        # Severity change with same (requirement, description) is the same gap.
        blocker = _gap("retry undefined", severity=GapSeverity.BLOCKER)
        minor = _gap("retry undefined", severity=GapSeverity.MINOR)
        assert gap_signature_for(blocker) == gap_signature_for(minor)

    def test_normalize_is_conservative(self) -> None:
        assert normalize_gap_description("  Foo   Bar ") == "foo bar"
        # Punctuation/meaning preserved (not stripped or rewritten).
        assert normalize_gap_description("A, B; C?") == "a, b; c?"


class TestClassification:
    def test_current_unseen_gap_is_new(self) -> None:
        report = GapReport(gaps=[_gap("brand new gap")])
        diff = diff_gaps(report, asked_signatures=[], accepted_signatures=[])
        assert len(diff.new) == 1
        assert diff.current[0].classification is GapClassification.NEW

    def test_previously_asked_gap_is_persisting(self) -> None:
        report = GapReport(gaps=[_gap("already asked")])
        asked = [gap_signature("REQ-001", "already asked")]
        diff = diff_gaps(report, asked_signatures=asked)
        assert len(diff.persisting) == 1
        assert diff.current[0].classification is GapClassification.PERSISTING
        assert diff.new == ()

    def test_previously_asked_gap_absent_is_resolved(self) -> None:
        report = GapReport(gaps=[_gap("still here")])
        asked = [
            gap_signature("REQ-001", "still here"),
            gap_signature("REQ-001", "gone now"),
        ]
        diff = diff_gaps(report, asked_signatures=asked)
        assert diff.resolved_signatures == (gap_signature("REQ-001", "gone now"),)

    def test_accepted_assumption_is_accepted(self) -> None:
        report = GapReport(gaps=[_gap("accepted gap")])
        accepted = [gap_signature("REQ-001", "accepted gap")]
        diff = diff_gaps(report, asked_signatures=[], accepted_signatures=accepted)
        assert len(diff.accepted) == 1
        assert diff.current[0].classification is GapClassification.ACCEPTED
        # An accepted gap is never NEW.
        assert diff.new == ()

    def test_accepted_takes_precedence_over_asked(self) -> None:
        sig = gap_signature("REQ-001", "both asked and accepted")
        report = GapReport(gaps=[_gap("both asked and accepted")])
        diff = diff_gaps(report, asked_signatures=[sig], accepted_signatures=[sig])
        assert diff.current[0].classification is GapClassification.ACCEPTED

    def test_accepted_not_present_is_not_resolved(self) -> None:
        # An accepted signature that has disappeared must NOT surface as resolved
        # (it was consciously accepted, not merely asked).
        report = GapReport(gaps=[_gap("present")])
        accepted = [gap_signature("REQ-001", "accepted and gone")]
        asked = [gap_signature("REQ-001", "accepted and gone")]
        diff = diff_gaps(report, asked_signatures=asked, accepted_signatures=accepted)
        assert diff.resolved_signatures == ()

    def test_mixed_classification(self) -> None:
        report = GapReport(gaps=[_gap("new one"), _gap("asked one"), _gap("accepted one")])
        asked = [
            gap_signature("REQ-001", "asked one"),
            gap_signature("REQ-001", "vanished one"),
        ]
        accepted = [gap_signature("REQ-001", "accepted one")]
        diff = diff_gaps(report, asked_signatures=asked, accepted_signatures=accepted)
        kinds = [c.classification for c in diff.current]
        assert kinds == [
            GapClassification.NEW,
            GapClassification.PERSISTING,
            GapClassification.ACCEPTED,
        ]
        assert diff.resolved_signatures == (gap_signature("REQ-001", "vanished one"),)

    def test_duplicate_current_gaps_handled_deterministically(self) -> None:
        # Two current gaps with the same signature are each classified and retained
        # in report order (caller decides de-duplication downstream).
        report = GapReport(gaps=[_gap("dup gap"), _gap("dup gap")])
        diff = diff_gaps(report)
        assert len(diff.current) == 2
        assert diff.current[0].signature == diff.current[1].signature
        assert all(c.classification is GapClassification.NEW for c in diff.current)

    def test_multiple_requirements_similar_descriptions(self) -> None:
        # Same description under different requirements => different signatures =>
        # both NEW, never merged.
        report = GapReport(
            gaps=[
                _gap("missing acceptance criteria", requirement_id="REQ-001"),
                _gap("missing acceptance criteria", requirement_id="REQ-002"),
            ]
        )
        diff = diff_gaps(report)
        assert len(diff.new) == 2
        assert diff.current[0].signature != diff.current[1].signature

    def test_empty_gap_report(self) -> None:
        diff = diff_gaps(GapReport(gaps=[]), asked_signatures=["REQ-001\x1fgone"])
        assert diff.current == ()
        # An asked signature with nothing present becomes resolved.
        assert diff.resolved_signatures == ("REQ-001\x1fgone",)

    def test_empty_history(self) -> None:
        report = GapReport(gaps=[_gap("a"), _gap("b")])
        diff = diff_gaps(report)
        assert len(diff.new) == 2
        assert diff.resolved_signatures == ()

    def test_none_requirement_gap_classifies(self) -> None:
        report = GapReport(gaps=[_gap("no req gap", requirement_id=None)])
        asked = [gap_signature(None, "no req gap")]
        diff = diff_gaps(report, asked_signatures=asked)
        assert diff.current[0].classification is GapClassification.PERSISTING


class TestPurity:
    def test_input_state_not_mutated(self) -> None:
        state = ClarificationState(
            run_id="r1", asked_gap_signatures=[gap_signature("REQ-001", "asked")]
        )
        before = list(state.asked_gap_signatures)
        report = GapReport(gaps=[_gap("asked"), _gap("new")])
        diff_gaps(report, asked_signatures=state.asked_gap_signatures)
        assert state.asked_gap_signatures == before  # unchanged

    def test_input_report_not_mutated(self) -> None:
        report = GapReport(gaps=[_gap("g1"), _gap("g2")])
        before = [g.description for g in report.gaps]
        diff_gaps(report, asked_signatures=[gap_signature("REQ-001", "g1")])
        assert [g.description for g in report.gaps] == before

    def test_asked_list_argument_not_modified(self) -> None:
        asked = [gap_signature("REQ-001", "asked")]
        report = GapReport(gaps=[_gap("new")])
        diff_gaps(report, asked_signatures=asked)
        assert asked == [gap_signature("REQ-001", "asked")]

    def test_repeated_execution_is_deterministic(self) -> None:
        report = GapReport(gaps=[_gap("a"), _gap("b"), _gap("c")])
        asked = [gap_signature("REQ-001", "a"), gap_signature("REQ-001", "gone")]
        accepted = [gap_signature("REQ-001", "b")]
        first = diff_gaps(report, asked_signatures=asked, accepted_signatures=accepted)
        second = diff_gaps(report, asked_signatures=asked, accepted_signatures=accepted)
        assert [c.classification for c in first.current] == [
            c.classification for c in second.current
        ]
        assert first.resolved_signatures == second.resolved_signatures
        assert [c.signature for c in first.current] == [c.signature for c in second.current]
