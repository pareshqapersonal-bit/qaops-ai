"""Phase 27 tests: the goal-driven agent loop (ADR-042).

Covers the required matrix: observe, continue, resume loop, stop conditions,
clarification/manual-review/retry recommendations, reflection accumulation,
deterministic identity, and the three "cannot" invariants (execute stages,
generate artifacts, modify checkpoints). Includes a direct-vs-agent artifact
identity comparison.

All runs use MockLLMClient; no live LLM.
"""

import json
from pathlib import Path

from qaops.agent import (
    GoalDrivenLoop,
    LoopDecision,
    Observation,
    OrchestratorAgent,
    Reflector,
    TerminalReason,
    decide,
    observe,
)
from qaops.config import QAOpsSettings
from qaops.execution.checkpoint import CheckpointStore
from qaops.llm import LLMProviderError, MockLLMClient
from qaops.models import Requirement, RequirementAnalysisResult
from qaops.services import DesignService

# --- observe (read-only) ----------------------------------------------------


def _analysis() -> RequirementAnalysisResult:
    return RequirementAnalysisResult(
        source_name="p.md",
        source_text="t",
        requirements=[Requirement(id="REQ-001", title="R", description="D")],
        business_rules=[],
        gap_report={"gaps": []},
    )


class TestObserve:
    def test_observe_reads_completed_stages(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path)
        store.write_stage("requirement_analyzer", 0, _analysis())
        obs = observe(
            iteration=1,
            resume_attempts=0,
            checkpoints=store,
            result=None,
            failed_stage="business_rule_extractor",
            prior_failed_stages=[],
        )
        assert obs.completed_stages == ["requirement_analyzer"]
        assert obs.failed_stage == "business_rule_extractor"
        assert obs.succeeded is False

    def test_observe_detects_repeated_failure(self, tmp_path: Path) -> None:
        obs = observe(
            iteration=2,
            resume_attempts=1,
            checkpoints=CheckpointStore(tmp_path),
            result=None,
            failed_stage="gap_analyzer",
            prior_failed_stages=["gap_analyzer"],
        )
        assert obs.repeated_failure is True

    def test_observe_is_read_only(self, tmp_path: Path) -> None:
        # observe must not create or write any checkpoint file.
        store = CheckpointStore(tmp_path)
        observe(
            iteration=1,
            resume_attempts=0,
            checkpoints=store,
            result=None,
            failed_stage=None,
            prior_failed_stages=[],
        )
        assert not store.directory.exists() or not any(store.directory.glob("*.json"))


# --- decide -----------------------------------------------------------------


def _obs(**kw) -> Observation:  # type: ignore[no-untyped-def]
    base = dict(
        iteration=1,
        resume_attempts=0,
        succeeded=False,
        completed_stages=[],
        failed_stage=None,
        repeated_failure=False,
        unresolved_conditions=0,
        total_conditions=0,
        gap_count=0,
    )
    base.update(kw)
    return Observation(**base)


class TestDecide:
    def test_success_finishes(self) -> None:
        d, _ = decide(_obs(succeeded=True, total_conditions=4), max_resume_attempts=2)
        assert d is LoopDecision.CONTINUE

    def test_resume_when_checkpoints_exist(self) -> None:
        d, rec = decide(
            _obs(failed_stage="gap_analyzer", completed_stages=["requirement_analyzer"]),
            max_resume_attempts=2,
        )
        assert d is LoopDecision.RESUME
        assert rec.alternative_considered  # restart considered
        assert rec.rejected_because

    def test_stop_when_nothing_to_resume(self) -> None:
        d, _ = decide(
            _obs(failed_stage="requirement_analyzer", completed_stages=[]), max_resume_attempts=2
        )
        assert d is LoopDecision.STOP

    def test_manual_review_on_repeated_failure(self) -> None:
        d, _ = decide(
            _obs(
                failed_stage="gap_analyzer",
                completed_stages=["requirement_analyzer"],
                repeated_failure=True,
            ),
            max_resume_attempts=2,
        )
        assert d is LoopDecision.RECOMMEND_MANUAL_REVIEW

    def test_stop_at_max_resume_attempts(self) -> None:
        d, _ = decide(
            _obs(
                failed_stage="gap_analyzer",
                completed_stages=["requirement_analyzer"],
                resume_attempts=2,
            ),
            max_resume_attempts=2,
        )
        assert d is LoopDecision.STOP

    def test_clarification_on_high_unresolved(self) -> None:
        d, _ = decide(
            _obs(succeeded=True, unresolved_conditions=2, total_conditions=4),
            max_resume_attempts=2,
        )
        assert d is LoopDecision.RECOMMEND_CLARIFICATION

    def test_clarification_on_many_gaps(self) -> None:
        d, _ = decide(_obs(succeeded=True, total_conditions=4, gap_count=6), max_resume_attempts=2)
        assert d is LoopDecision.RECOMMEND_CLARIFICATION


# --- end-to-end loop --------------------------------------------------------

_SCRIPT = [
    json.dumps({"requirements": [{"title": "Login", "description": "User logs in."}]}),
    json.dumps({"rules": [{"requirement_id": "REQ-001", "rule": "pw>=8", "source_excerpt": "8"}]}),
    json.dumps({"gaps": []}),
    json.dumps(
        {
            "scenarios": [
                {
                    "title": "Login",
                    "description": "ok",
                    "category": "positive",
                    "requirement_ids": ["REQ-001"],
                }
            ]
        }
    ),
    json.dumps(
        {
            "conditions": [
                {
                    "scenario_id": "SC-001",
                    "requirement_ids": ["REQ-001"],
                    "business_rule_ids": ["BR-001"],
                    "category": "positive",
                    "description": "valid",
                    "rationale": "REQ-001",
                    "source_basis": "explicit_requirement",
                    "status": "resolved",
                    "parameters": {},
                    "gap_reference": "",
                }
            ]
        }
    ),
    json.dumps(
        {
            "test_cases": [
                {
                    "scenario_id": "SC-001",
                    "condition_id": "COND-001",
                    "slot_id": "COND-001-S1",
                    "requirement_ids": ["REQ-001"],
                    "title": "Login",
                    "expected_result": "ok",
                    "steps": [{"action": "login", "expected": "ok"}],
                    "priority": "high",
                    "test_type": "functional",
                }
            ]
        }
    ),
]


def _doc(tmp_path: Path) -> Path:
    p = tmp_path / "prd.md"
    p.write_text("# Login\nUser logs in with password >= 8.\n")
    return p


def _snapshot(r):  # type: ignore[no-untyped-def]
    return json.dumps(
        {
            "requirements": [x.model_dump(mode="json") for x in r.requirements],
            "business_rules": [x.model_dump(mode="json") for x in r.business_rules],
            "gaps": [g.model_dump(mode="json") for g in r.gap_report.gaps],
            "scenarios": [x.model_dump(mode="json") for x in r.scenarios],
            "conditions": [x.model_dump(mode="json") for x in r.conditions],
            "test_cases": [x.model_dump(mode="json") for x in r.test_cases],
            "coverage": r.coverage.model_dump(mode="json"),
        },
        sort_keys=True,
    )


class TestGoalDrivenLoopEndToEnd:
    def test_single_success_one_iteration_and_completes(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import qaops.services.design_service as ds

        s = QAOpsSettings(output_dir=tmp_path / "o", provider="mock")
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(list(_SCRIPT)))
        _, outcome, summary = OrchestratorAgent(DesignService()).execute_until_goal(
            _doc(tmp_path), s
        )
        assert summary.terminal_reason == TerminalReason.COMPLETED.value
        assert len(summary.iterations) == 1
        assert summary.reflection.goal_achieved is True
        assert outcome is not None

    def test_direct_vs_agent_artifacts_identical(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import qaops.services.design_service as ds

        s1 = QAOpsSettings(output_dir=tmp_path / "a", provider="mock")
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(list(_SCRIPT)))
        direct = DesignService().run(_doc(tmp_path), s1)

        s2 = QAOpsSettings(output_dir=tmp_path / "b", provider="mock")
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(list(_SCRIPT)))
        _, outcome, _ = OrchestratorAgent(DesignService()).execute_until_goal(_doc(tmp_path), s2)

        assert _snapshot(direct.result) == _snapshot(outcome.result)

    def test_resume_loop_recovers_across_iterations(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import qaops.services.design_service as ds

        s = QAOpsSettings(output_dir=tmp_path / "o", provider="mock")
        doc = _doc(tmp_path)
        # First act fails at test_condition_analyzer (index 4); a resume then
        # supplies the remaining responses and completes.
        scripts = iter(
            [
                _SCRIPT[:4] + [LLMProviderError("mock", "x")] * 3,  # first act: fail at cond
                _SCRIPT[4:],  # resume act: cond + cases
            ]
        )
        monkeypatch.setattr(
            ds, "create_client", lambda settings: MockLLMClient(list(next(scripts)))
        )
        _, outcome, summary = OrchestratorAgent(DesignService()).execute_until_goal(doc, s)
        assert outcome is not None
        assert summary.terminal_reason == TerminalReason.COMPLETED.value
        # Two iterations: the failed act, then the successful resume.
        assert len(summary.iterations) == 2
        assert summary.resume_attempts == 1
        assert summary.reflection.goal_achieved is True

    def test_stop_at_max_resume_attempts(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import qaops.services.design_service as ds

        # max_resume_attempts=1: fail at condition, resume once, fail again -> stop.
        s = QAOpsSettings(output_dir=tmp_path / "o", provider="mock", max_resume_attempts=1)
        doc = _doc(tmp_path)
        fail_at_cond = _SCRIPT[:4] + [LLMProviderError("mock", "x")] * 3
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(list(fail_at_cond)))
        _, outcome, summary = OrchestratorAgent(DesignService()).execute_until_goal(doc, s)
        assert outcome is None
        assert summary.terminal_reason in (
            TerminalReason.MAX_RESUME_ATTEMPTS.value,
            TerminalReason.NEEDS_MANUAL_REVIEW.value,
        )
        assert summary.reflection.needs_manual_review is True

    def test_no_completed_stage_stops_without_resume(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import qaops.services.design_service as ds

        s = QAOpsSettings(output_dir=tmp_path / "o", provider="mock")
        doc = _doc(tmp_path)
        # First stage fails immediately: nothing to resume.
        monkeypatch.setattr(
            ds, "create_client", lambda settings: MockLLMClient([LLMProviderError("mock", "x")] * 3)
        )
        _, outcome, summary = OrchestratorAgent(DesignService()).execute_until_goal(doc, s)
        assert outcome is None
        assert summary.resume_attempts == 0
        assert len(summary.iterations) == 1

    def test_reflection_accumulates_recommendations(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import qaops.services.design_service as ds

        s = QAOpsSettings(output_dir=tmp_path / "o", provider="mock")
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(list(_SCRIPT)))
        _, _, summary = OrchestratorAgent(DesignService()).execute_until_goal(_doc(tmp_path), s)
        assert summary.reflection.recommendations  # always has at least one


# --- invariants: cannot execute stages / generate artifacts / write checkpoints


class TestInvariants:
    def test_loop_only_delegates_to_service(self) -> None:
        # GoalDrivenLoop holds a service + reflector; it exposes only run().
        loop = GoalDrivenLoop(DesignService(), Reflector())
        public = {m for m in dir(loop) if not m.startswith("_")}
        assert public == {"run"}

    def test_agent_has_no_artifact_or_checkpoint_write_method(self) -> None:
        agent = OrchestratorAgent(DesignService())
        public = {m for m in dir(agent) if not m.startswith("_")}
        for forbidden in (
            "write_stage",
            "generate",
            "analyze",
            "extract_rules",
            "generate_scenarios",
        ):
            assert forbidden not in public

    def test_loop_does_not_write_checkpoints_itself(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import qaops.services.design_service as ds

        # After a successful run, the only checkpoints present are the ones the
        # pipeline wrote (7 stages). The loop adds none of its own.
        s = QAOpsSettings(output_dir=tmp_path / "o", provider="mock")
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(list(_SCRIPT)))
        OrchestratorAgent(DesignService()).execute_until_goal(_doc(tmp_path), s)
        store = CheckpointStore(s.output_dir)
        # All checkpoint files correspond to real pipeline stages.
        stage_files = {p.stem.split("_", 1)[1] for p in store.directory.glob("[0-9]*.json")}
        assert stage_files <= {
            "requirement_analyzer",
            "business_rule_extractor",
            "gap_analyzer",
            "scenario_generator",
            "test_condition_analyzer",
            "test_case_generator",
            "coverage_validator",
        }
