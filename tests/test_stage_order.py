"""Pins the DOCUMENT-entry-point stage order (frontend parity guard).

The frontend progress journey (PIPELINE_STAGES in useBackendStatus.ts) must list
exactly these stages, in this order. If this list changes here, the frontend
constant and its regression test (deriveStageStates.test.ts) must be updated to
match. Keeping the assertion on both sides catches drift in either direction.
"""

from qaops.entrypoints import stage_names_for
from qaops.entrypoints.entry_point import EntryPoint

# The canonical order the frontend mirrors. Duplicated intentionally as a
# literal so a change to stage_names_for is a visible, reviewable diff here.
_FRONTEND_DOCUMENT_STAGES = [
    "requirement_analyzer",
    "business_rule_extractor",
    "gap_analyzer",
    "scenario_generator",
    "test_condition_analyzer",
    "test_case_generator",
    "coverage_validator",
]


def test_document_stage_order_matches_frontend_journey() -> None:
    assert stage_names_for(EntryPoint.DOCUMENT) == _FRONTEND_DOCUMENT_STAGES


def test_document_pipeline_has_seven_stages() -> None:
    assert len(stage_names_for(EntryPoint.DOCUMENT)) == 7


def test_test_condition_analyzer_is_present_and_ordered() -> None:
    stages = stage_names_for(EntryPoint.DOCUMENT)
    assert "test_condition_analyzer" in stages
    # It sits between scenario generation and test-case generation.
    assert stages.index("scenario_generator") < stages.index("test_condition_analyzer")
    assert stages.index("test_condition_analyzer") < stages.index("test_case_generator")
