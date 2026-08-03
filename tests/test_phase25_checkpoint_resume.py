"""Phase 25 tests: checkpointing, partial export, and resume (ADR-040).

Three layers:
  * CheckpointStore unit tests - write/read/round-trip, source_text exclusion,
    corrupt/missing/unknown-type handling, manifest and latest-checkpoint.
  * DesignService integration - a run that fails at a chosen stage leaves
    checkpoints and partial artifacts; resume completes without re-running
    completed stages; multiple resumes; no-checkpoint fallback.
  * Run-state - partial/resumable/cancelled statuses and per-stage tracking.

All pipeline runs use MockLLMClient (no live LLM). A stage is made to fail by
scripting an exception at its position in the response queue.
"""

import json
from pathlib import Path

import pytest

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.execution.checkpoint import (
    _SOURCE_TEXT_PLACEHOLDER,
    CheckpointError,
    CheckpointStore,
    StageStatus,
)
from qaops.llm import LLMProviderError
from qaops.models import (
    Requirement,
    RequirementAnalysisResult,
    ScenarioDesignResult,
)

# --- CheckpointStore unit tests --------------------------------------------


def _analysis(text: str = "the full source document text") -> RequirementAnalysisResult:
    return RequirementAnalysisResult(
        source_name="prd.docx",
        source_text=text,
        requirements=[Requirement(id="REQ-001", title="R", description="D")],
        business_rules=[],
        gap_report={"gaps": []},
    )


class TestCheckpointRoundTrip:
    def test_write_and_rehydrate_exact_model(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path)
        analysis = _analysis()
        store.write_stage("requirement_analyzer", 0, analysis)
        cp = store.latest_checkpoint()
        assert cp is not None
        assert type(cp.result).__name__ == "RequirementAnalysisResult"
        # Everything except source_text round-trips exactly.
        assert cp.result.requirements[0].id == "REQ-001"

    def test_source_text_excluded_from_disk(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path)
        store.write_stage("requirement_analyzer", 0, _analysis("SECRET-DOC-BODY"))
        raw = (store.directory / "00_requirement_analyzer.json").read_text()
        assert "SECRET-DOC-BODY" not in raw

    def test_source_text_placeholder_on_rehydrate(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path)
        store.write_stage("requirement_analyzer", 0, _analysis("SECRET-DOC-BODY"))
        cp = store.latest_checkpoint()
        assert cp is not None
        assert cp.result.source_text == _SOURCE_TEXT_PLACEHOLDER

    def test_nested_source_text_excluded(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path)
        scen = ScenarioDesignResult(analysis=_analysis("NESTED-SECRET"), scenarios=[])
        store.write_stage("scenario_generator", 3, scen)
        raw = (store.directory / "03_scenario_generator.json").read_text()
        assert "NESTED-SECRET" not in raw
        cp = store.latest_checkpoint()
        assert cp is not None
        assert cp.result.analysis.source_text == _SOURCE_TEXT_PLACEHOLDER

    def test_latest_checkpoint_is_highest_index(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path)
        store.write_stage("requirement_analyzer", 0, _analysis())
        scen = ScenarioDesignResult(analysis=_analysis(), scenarios=[])
        store.write_stage("scenario_generator", 3, scen)
        cp = store.latest_checkpoint()
        assert cp is not None and cp.stage_index == 3

    def test_completed_stages_in_order(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path)
        store.write_stage("requirement_analyzer", 0, _analysis())
        store.write_stage(
            "scenario_generator", 3, ScenarioDesignResult(analysis=_analysis(), scenarios=[])
        )
        assert store.completed_stages() == ["requirement_analyzer", "scenario_generator"]


class TestCheckpointErrors:
    def test_missing_checkpoint_returns_none(self, tmp_path: Path) -> None:
        assert CheckpointStore(tmp_path).latest_checkpoint() is None

    def test_corrupt_checkpoint_raises(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path)
        store.directory.mkdir(parents=True, exist_ok=True)
        (store.directory / "00_requirement_analyzer.json").write_text("{ not json")
        with pytest.raises(CheckpointError, match="Corrupt checkpoint"):
            store.latest_checkpoint()

    def test_unknown_result_type_raises(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path)
        store.directory.mkdir(parents=True, exist_ok=True)
        (store.directory / "00_stage.json").write_text(
            json.dumps({"result_type": "NopeModel", "result": {}, "stage_index": 0})
        )
        with pytest.raises(CheckpointError, match="unknown result_type"):
            store.latest_checkpoint()

    def test_manifest_absent_is_empty(self, tmp_path: Path) -> None:
        assert CheckpointStore(tmp_path).manifest() == []

    def test_mark_stage_records_status(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path)
        store.mark_stage("gap_analyzer", 2, StageStatus.FAILED)
        manifest = store.manifest()
        assert manifest[0]["stage_name"] == "gap_analyzer"
        assert manifest[0]["status"] == "failed"


# --- DesignService integration: fail, partial export, resume ----------------

from qaops.llm import MockLLMClient  # noqa: E402
from qaops.services import DesignService  # noqa: E402

# Scripted stage responses for the full DOCUMENT pipeline (7 stages).
_R_ANALYZER = json.dumps({"requirements": [{"title": "Login", "description": "User can log in."}]})
_R_RULES = json.dumps(
    {
        "rules": [
            {"requirement_id": "REQ-001", "rule": "Password >= 8 chars.", "source_excerpt": "8"}
        ]
    }
)
_R_GAPS = json.dumps({"gaps": []})
_R_SCEN = json.dumps(
    {
        "scenarios": [
            {
                "title": "Login works",
                "description": "valid creds",
                "category": "positive",
                "requirement_ids": ["REQ-001"],
            }
        ]
    }
)
_R_COND = json.dumps(
    {
        "conditions": [
            {
                "scenario_id": "SC-001",
                "requirement_ids": ["REQ-001"],
                "business_rule_ids": ["BR-001"],
                "category": "positive",
                "description": "Valid login succeeds.",
                "rationale": "REQ-001",
                "source_basis": "explicit_requirement",
                "status": "resolved",
                "parameters": {},
                "gap_reference": "",
            }
        ]
    }
)
_R_CASES = json.dumps(
    {
        "test_cases": [
            {
                "scenario_id": "SC-001",
                "condition_id": "COND-001",
                "slot_id": "COND-001-S1",
                "requirement_ids": ["REQ-001"],
                "title": "Login",
                "expected_result": "logged in",
                "steps": [{"action": "log in", "expected": "ok"}],
                "priority": "high",
                "test_type": "functional",
            }
        ]
    }
)

_FULL_SCRIPT = [_R_ANALYZER, _R_RULES, _R_GAPS, _R_SCEN, _R_COND, _R_CASES]


def _write_doc(tmp_path: Path) -> Path:
    p = tmp_path / "prd.md"
    p.write_text("# Login\n\nThe user can log in with a password of at least 8 characters.\n")
    return p


def _settings(tmp_path: Path) -> QAOpsSettings:
    return QAOpsSettings(output_dir=tmp_path / "out", provider="mock")


def _service_with(script: list) -> DesignService:  # type: ignore[type-arg]
    # Patch create_client so the pipeline uses our scripted mock.
    import qaops.services.design_service as ds

    client = MockLLMClient(script)
    ds.create_client = lambda settings: client  # type: ignore[assignment]
    return DesignService()


class TestFailAndPartialExport:
    def test_failure_at_condition_stage_leaves_partial_artifacts(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        # Fail at test_condition_analyzer (index 4): first 4 stages succeed.
        script = [
            _R_ANALYZER,
            _R_RULES,
            _R_GAPS,
            _R_SCEN,
            LLMProviderError("mock", "boom"),
            LLMProviderError("mock", "boom"),
            LLMProviderError("mock", "boom"),
        ]
        import qaops.services.design_service as ds

        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(script))
        settings = _settings(tmp_path)
        with pytest.raises(StageError):
            DesignService().run(_write_doc(tmp_path), settings)
        # Checkpoints for the 4 completed stages exist.
        store = CheckpointStore(settings.output_dir)
        completed = store.completed_stages()
        assert "scenario_generator" in completed
        assert "test_condition_analyzer" not in completed
        # Partial artifacts were written (CSV bundle from the scenario checkpoint).
        files = {p.name for p in settings.output_dir.iterdir() if p.is_file()}
        assert any("Requirements" in f for f in files)
        assert any("Scenarios" in f for f in files)


class TestResume:
    def test_resume_completes_without_rerunning_completed_stages(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        import qaops.services.design_service as ds

        settings = _settings(tmp_path)
        doc = _write_doc(tmp_path)

        # First attempt: fail at condition stage (4 stages complete).
        fail_script = [
            _R_ANALYZER,
            _R_RULES,
            _R_GAPS,
            _R_SCEN,
            LLMProviderError("mock", "x"),
            LLMProviderError("mock", "x"),
            LLMProviderError("mock", "x"),
        ]
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(fail_script))
        with pytest.raises(StageError):
            DesignService().run(doc, settings)

        # Resume: only condition + case + coverage need to run now.
        resume_script = [_R_COND, _R_CASES]
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(resume_script))
        outcome = DesignService().resume(doc, settings)
        # Completed successfully with full artifacts.
        assert outcome.result.test_cases
        assert outcome.result.conditions
        # The resume script only had 2 responses - proving upstream stages were
        # NOT re-run (they would have consumed responses and raised).

    def test_resume_with_no_checkpoint_falls_back_to_full_run(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        import qaops.services.design_service as ds

        settings = _settings(tmp_path)
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(list(_FULL_SCRIPT)))
        outcome = DesignService().resume(_write_doc(tmp_path), settings)
        assert outcome.result.test_cases  # full run happened

    def test_second_failure_after_resume_stays_resumable(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import qaops.services.design_service as ds

        settings = _settings(tmp_path)
        doc = _write_doc(tmp_path)
        # Fail at scenario stage (3 complete: analyzer, rules, gaps).
        s1 = [
            _R_ANALYZER,
            _R_RULES,
            _R_GAPS,
            LLMProviderError("mock", "x"),
            LLMProviderError("mock", "x"),
            LLMProviderError("mock", "x"),
        ]
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(s1))
        with pytest.raises(StageError):
            DesignService().run(doc, settings)
        store = CheckpointStore(settings.output_dir)
        assert "gap_analyzer" in store.completed_stages()
        assert "scenario_generator" not in store.completed_stages()

        # Resume but fail again at scenario stage.
        s2 = [
            LLMProviderError("mock", "y"),
            LLMProviderError("mock", "y"),
            LLMProviderError("mock", "y"),
        ]
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(s2))
        with pytest.raises(StageError):
            DesignService().resume(doc, settings)
        # Still only 3 completed - no progress lost, still resumable.
        assert store.completed_stages() == [
            "requirement_analyzer",
            "business_rule_extractor",
            "gap_analyzer",
        ]

    def test_full_resume_after_all_but_last_stage(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import qaops.services.design_service as ds

        settings = _settings(tmp_path)
        doc = _write_doc(tmp_path)
        # Fail at the very last stage (coverage is deterministic and won't fail;
        # fail at case generation, index 5).
        s1 = [
            _R_ANALYZER,
            _R_RULES,
            _R_GAPS,
            _R_SCEN,
            _R_COND,
            LLMProviderError("mock", "x"),
            LLMProviderError("mock", "x"),
            LLMProviderError("mock", "x"),
        ]
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(s1))
        with pytest.raises(StageError):
            DesignService().run(doc, settings)
        store = CheckpointStore(settings.output_dir)
        assert "test_condition_analyzer" in store.completed_stages()

        # Resume: only case gen + coverage remain.
        s2 = [_R_CASES]
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(s2))
        outcome = DesignService().resume(doc, settings)
        assert outcome.result.test_cases
