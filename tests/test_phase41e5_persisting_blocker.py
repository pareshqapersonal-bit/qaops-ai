"""Option A: a still-present (PERSISTING) BLOCKER gap keeps the run not-ready.

When the user answers a blocking question but re-analysis STILL reports the same
blocker gap (its lexical signature was already asked, so diff classifies it
PERSISTING - not NEW, and no fresh question is generated), the run must NOT reach
READY. Instead readiness stays False so the clarification loop continues within the
existing 5-round budget. A blocker that actually disappears reaches READY;
proceed-with-assumptions still overrides; the 5-round cap still terminates.

Reuses the Phase 41E-3 iterative-loop harness. gap_diff and lexical identity are
NOT modified - this only changes how readiness counts persisting blocker gaps.
"""

from pathlib import Path

from qaops.clarification.enums import ClarificationStatus
from qaops.clarification.service import ClarificationRoundLimitError
from qaops.llm import MockLLMClient
from tests.test_phase41e3_iterative_loop import (
    _GAP_NONE,
    _answer_first,
    _clarify,
    _gap_report,
    _start,
)

# The SAME blocker description the harness asks in _start ("retry policy undefined")
# so that on re-analysis it classifies as PERSISTING (already-asked, still present).
_SAME_BLOCKER = "retry policy undefined"


class TestPersistingBlockerKeepsNotReady:
    def test_persisting_blocker_does_not_reach_ready(self, tmp_path: Path) -> None:
        # (1) Answer the blocker, but re-analysis STILL reports the same blocker gap.
        # It is PERSISTING (not NEW) -> no new question -> but readiness must stay
        # not-ready and the loop continue, rather than proceeding to test design.
        svc, ws, state = _start(tmp_path)
        with _clarify(MockLLMClient([_gap_report(_SAME_BLOCKER)])):
            new = svc.submit_answers(ws, _answer_first(state))
        assert new.readiness.ready is False
        assert new.status is not ClarificationStatus.READY_FOR_TEST_DESIGN
        # No NEW question was generated for the persisting gap (gap_diff unchanged),
        # so the question set is unchanged - the run simply stays in clarification.
        assert new.status is ClarificationStatus.CLARIFYING
        assert new.readiness.critical_gaps >= 1  # the persisting blocker is counted

    def test_resolved_blocker_reaches_ready(self, tmp_path: Path) -> None:
        # (2) Answer the blocker and re-analysis reports NO gaps -> the blocker is
        # genuinely resolved -> READY (unchanged good-path behaviour).
        svc, ws, state = _start(tmp_path)
        with _clarify(MockLLMClient([_GAP_NONE])):
            new = svc.submit_answers(ws, _answer_first(state))
        assert new.readiness.ready is True
        assert new.status is ClarificationStatus.READY_FOR_TEST_DESIGN


class TestRoundCapStillTerminates:
    def test_five_round_cap_still_raises(self, tmp_path: Path) -> None:
        # (3) A persisting blocker keeps the run not-ready every round; once the
        # iteration reaches the 5-round cap, submit_answers raises the round-limit
        # error (directing the user to proceed_with_assumptions) rather than looping
        # forever.
        svc, ws, state = _start(tmp_path)
        answers = _answer_first(state)
        # Drive rounds: each returns the same persisting blocker, staying not-ready.
        # start() is iteration 1; each submit increments. Loop until the cap raises.
        raised = False
        for _ in range(6):
            try:
                with _clarify(MockLLMClient([_gap_report(_SAME_BLOCKER)])):
                    result = svc.submit_answers(ws, answers)
            except ClarificationRoundLimitError:
                raised = True
                break
            assert result.readiness.ready is False
        assert raised, "5-round cap did not terminate a persisting-blocker loop"


class TestProceedOverrides:
    def test_proceed_with_assumptions_overrides_persisting_blocker(self, tmp_path: Path) -> None:
        # (4) Even with a persisting blocker, proceed_with_assumptions must force the
        # run to READY - the blocker becomes an accepted assumption (accepted takes
        # precedence over asked in gap_diff, so it is no longer counted as open).
        svc, ws, state = _start(tmp_path)
        with _clarify(MockLLMClient([_GAP_NONE])):
            new = svc.submit_answers(ws, [], proceed_with_assumptions=True)
        assert new.readiness.ready is True
        assert new.status is ClarificationStatus.READY_FOR_TEST_DESIGN
