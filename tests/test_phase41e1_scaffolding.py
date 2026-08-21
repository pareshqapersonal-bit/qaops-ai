"""Phase 41E-1 tests: additive state/enum scaffolding for iterative clarification.

Scaffolding ONLY - no behaviour. Confirms the new lifecycle values and the new
ClarificationState history field exist, persist/reload correctly, and - critically -
that a pre-41E persisted ClarificationState payload (without the new field) still
loads. Existing 41A/41B/41C values and fields are asserted unchanged.
"""

from pathlib import Path

from qaops.api.runs import RunStatus
from qaops.clarification.enums import (
    AssumptionSource,
    ClarificationStatus,
)
from qaops.clarification.models import ClarificationState
from qaops.clarification.state_store import (
    clarification_state_path,
    load_clarification_state,
    write_clarification_state,
)


class TestNewEnumValues:
    def test_clarification_status_proceeded_added(self) -> None:
        assert ClarificationStatus.PROCEEDED.value == "proceeded"

    def test_assumption_source_user_proceed_unresolved_added(self) -> None:
        assert AssumptionSource.USER_PROCEED_UNRESOLVED.value == "user_proceed_unresolved"

    def test_run_status_user_proceeded_added(self) -> None:
        assert RunStatus.USER_PROCEEDED.value == "user_proceeded"

    def test_existing_clarification_status_values_unchanged(self) -> None:
        # The pre-41E values must all still exist with identical string values.
        assert ClarificationStatus.ANALYZING.value == "analyzing"
        assert ClarificationStatus.CLARIFYING.value == "clarifying"
        assert ClarificationStatus.RE_ANALYZING.value == "re_analyzing"
        assert ClarificationStatus.READY_FOR_TEST_DESIGN.value == "ready_for_test_design"

    def test_existing_assumption_sources_unchanged(self) -> None:
        assert AssumptionSource.USER_SKIP.value == "user_skip"
        assert AssumptionSource.AGENT_DEFAULT.value == "agent_default"

    def test_existing_run_status_clarification_values_unchanged(self) -> None:
        assert RunStatus.AWAITING_CLARIFICATION.value == "awaiting_clarification"
        assert RunStatus.READY_FOR_TEST_DESIGN.value == "ready_for_test_design"


class TestNewStateField:
    def test_default_is_empty_list(self) -> None:
        state = ClarificationState(run_id="r1")
        assert state.asked_gap_signatures == []

    def test_field_persists_and_reloads(self, tmp_path: Path) -> None:
        state = ClarificationState(
            run_id="r1",
            asked_gap_signatures=["REQ-001|retry-undefined", "REQ-002|timeout"],
        )
        write_clarification_state(tmp_path, state)
        reloaded = load_clarification_state(tmp_path)
        assert reloaded is not None
        assert reloaded.asked_gap_signatures == [
            "REQ-001|retry-undefined",
            "REQ-002|timeout",
        ]

    def test_existing_fields_unchanged(self) -> None:
        # The pre-41E field set is intact and behaves as before.
        state = ClarificationState(run_id="r1", iteration=3)
        assert state.run_id == "r1"
        assert state.iteration == 3
        assert state.status is ClarificationStatus.ANALYZING
        assert state.questions == []
        assert state.answers == []
        assert state.assumptions == []
        assert state.readiness.ready is False


class TestBackwardCompatiblePersistence:
    def test_pre_41e_payload_without_new_field_loads(self, tmp_path: Path) -> None:
        # A ClarificationState JSON written before 41E-1 has no asked_gap_signatures.
        # It must still load, with the new field defaulting to [].
        legacy_json = (
            '{"run_id": "legacy-run", "iteration": 2, "status": "clarifying", '
            '"questions": [], "answers": [], "assumptions": [], '
            '"readiness": {"ready": false, "requirements_total": 5, '
            '"blocking_unanswered": 1, "recommended_unanswered": 0, '
            '"optional_unanswered": 0, "critical_gaps": 1, "blocking_reasons": []}}'
        )
        # Write at the real state path the store reads from.
        target = clarification_state_path(tmp_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(legacy_json, encoding="utf-8")

        reloaded = load_clarification_state(tmp_path)
        assert reloaded is not None
        # New field safely defaulted.
        assert reloaded.asked_gap_signatures == []
        # Existing data preserved exactly.
        assert reloaded.run_id == "legacy-run"
        assert reloaded.iteration == 2
        assert reloaded.status is ClarificationStatus.CLARIFYING
        assert reloaded.readiness.requirements_total == 5
        assert reloaded.readiness.blocking_unanswered == 1

    def test_new_field_round_trips_through_json(self, tmp_path: Path) -> None:
        state = ClarificationState(run_id="r2", asked_gap_signatures=["a|b", "c|d"])
        write_clarification_state(tmp_path, state)
        reloaded = load_clarification_state(tmp_path)
        assert reloaded is not None
        assert reloaded.model_dump(mode="json")["asked_gap_signatures"] == ["a|b", "c|d"]
