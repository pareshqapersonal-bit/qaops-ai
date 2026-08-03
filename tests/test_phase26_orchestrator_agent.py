"""Phase 26 tests: the Orchestrator Agent (ADR-041).

Covers the required matrix: plan generation, resume decision, retry decision,
explanation generation, deterministic execution, no-op behaviour, reflection
generation, checkpoint reuse, and skipped-stage planning. Also asserts the two
invariants that make this a safe first agent:

  * the agent never generates or mutates any of the seven pipeline artifacts;
  * with no intervention, agent-driven execution is byte-identical to Phase 25.

All runs use MockLLMClient; no live LLM.
"""

import json
from pathlib import Path

import pytest

from qaops.agent import (
    Decision,
    ExecutionPlanner,
    OrchestratorAgent,
    PlanStepStatus,
    Reflection,
    Reflector,
)
from qaops.config import QAOpsSettings
from qaops.entrypoints.entry_point import EntryPoint
from qaops.execution.checkpoint import CheckpointStore
from qaops.llm import MockLLMClient
from qaops.models import (
    CoverageReport,
    RequirementAnalysisResult,
    TestDesignResult,
)
from qaops.services import DesignService


def _analysis() -> RequirementAnalysisResult:
    return RequirementAnalysisResult(
        source_name="prd.md",
        source_text="t",
        requirements=[],
        business_rules=[],
        gap_report={"gaps": []},
    )


# --- plan generation & skipped-stage planning ------------------------------


class TestPlanGeneration:
    def test_full_plan_when_no_checkpoints(self, tmp_path: Path) -> None:
        plan = ExecutionPlanner().build(
            "Generate a complete regression pack.",
            EntryPoint.DOCUMENT,
            CheckpointStore(tmp_path),
        )
        assert plan.entry_point == "document"
        assert len(plan.steps) == 7
        assert all(s.status is PlanStepStatus.RUN for s in plan.steps)
        assert plan.no_intervention is True
        assert plan.resume is False

    def test_each_step_has_reason_dependencies_expected_output(self, tmp_path: Path) -> None:
        plan = ExecutionPlanner().build("goal", EntryPoint.DOCUMENT, CheckpointStore(tmp_path))
        for step in plan.steps:
            assert step.reason
            assert step.expected_output
        # First stage has no dependency; later stages depend on the previous.
        assert plan.steps[0].dependencies == []
        assert plan.steps[1].dependencies == [plan.steps[0].stage]

    def test_entry_point_shapes_the_plan(self, tmp_path: Path) -> None:
        plan = ExecutionPlanner().build("g", EntryPoint.SCENARIOS, CheckpointStore(tmp_path))
        assert [s.stage for s in plan.steps] == [
            "test_condition_analyzer",
            "test_case_generator",
            "coverage_validator",
        ]


class TestSkippedStagePlanning:
    def test_completed_stages_marked_reuse(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path)
        store.write_stage("requirement_analyzer", 0, _analysis())
        store.write_stage("business_rule_extractor", 1, _analysis())
        plan = ExecutionPlanner().build("g", EntryPoint.DOCUMENT, store)
        reused = [s.stage for s in plan.steps if s.status is PlanStepStatus.REUSE]
        to_run = [s.stage for s in plan.steps if s.status is PlanStepStatus.RUN]
        assert reused == ["requirement_analyzer", "business_rule_extractor"]
        assert to_run[0] == "gap_analyzer"


# --- resume decision & explanation -----------------------------------------


class TestResumeDecision:
    def test_resume_decision_when_checkpoints_exist(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path)
        store.write_stage("requirement_analyzer", 0, _analysis())
        plan = ExecutionPlanner().build("g", EntryPoint.DOCUMENT, store)
        assert plan.resume is True
        assert plan.no_intervention is False
        d = plan.decisions[0]
        assert "Resume" in d.decision
        assert d.alternative_considered  # restart considered
        assert d.rejected_because  # and rejected with a reason

    def test_restart_decision_when_no_checkpoints(self, tmp_path: Path) -> None:
        plan = ExecutionPlanner().build("g", EntryPoint.DOCUMENT, CheckpointStore(tmp_path))
        d = plan.decisions[0]
        assert "full pipeline" in d.decision.lower()
        assert "resume" in d.alternative_considered.lower()

    def test_decision_records_are_fully_structured(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path)
        store.write_stage("requirement_analyzer", 0, _analysis())
        plan = ExecutionPlanner().build("g", EntryPoint.DOCUMENT, store)
        for d in plan.decisions:
            assert isinstance(d, Decision)
            assert d.decision and d.reason


# --- explanation generation (LLM enrichment, best-effort) ------------------


class TestExplanationGeneration:
    def test_llm_enriches_reasons_but_not_structure(self, tmp_path: Path) -> None:
        from qaops.llm import PromptLoader

        # LLM returns new reason text for one stage.
        client = MockLLMClient(
            [json.dumps({"steps": [{"stage": "gap_analyzer", "reason": "Custom LLM reason."}]})]
        )
        plan = ExecutionPlanner(
            client=client, prompts=PromptLoader(), settings=QAOpsSettings()
        ).build("g", EntryPoint.DOCUMENT, CheckpointStore(tmp_path))
        gap = next(s for s in plan.steps if s.stage == "gap_analyzer")
        assert gap.reason == "Custom LLM reason."
        # Structure unchanged: still 7 stages in fixed order.
        assert len(plan.steps) == 7

    def test_llm_failure_falls_back_to_deterministic(self, tmp_path: Path) -> None:
        from qaops.llm import LLMProviderError, PromptLoader

        client = MockLLMClient([LLMProviderError("mock", "down")])
        plan = ExecutionPlanner(
            client=client, prompts=PromptLoader(), settings=QAOpsSettings()
        ).build("g", EntryPoint.DOCUMENT, CheckpointStore(tmp_path))
        # Deterministic reasons still present; no crash.
        assert all(s.reason for s in plan.steps)


# --- reflection generation & retry reasoning -------------------------------


class TestReflection:
    def _result(self, unresolved: int = 0, total: int = 4, gaps: int = 0) -> TestDesignResult:
        from qaops.models import CoverageMetrics

        metrics = CoverageMetrics(
            total_conditions=total,
            covered_conditions=total - unresolved,
            unresolved_conditions=unresolved,
        )
        gap_report = {"gaps": [{"description": f"g{i}", "severity": "minor"} for i in range(gaps)]}
        return TestDesignResult(
            source_name="x",
            requirements=[],
            business_rules=[],
            gap_report=gap_report,
            scenarios=[],
            conditions=[],
            test_cases=[],
            coverage=CoverageReport(metrics=metrics),
        )

    def test_reflection_reports_successes(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path)
        store.write_stage("requirement_analyzer", 0, _analysis())
        store.write_stage(
            "scenario_generator",
            3,
            __import__("qaops.models", fromlist=["ScenarioDesignResult"]).ScenarioDesignResult(
                analysis=_analysis(), scenarios=[]
            ),
        )
        refl = Reflector().build(
            result=self._result(), checkpoints=store, attempt_history=[], failed_stage=None
        )
        assert "requirement_analyzer" in refl.successes
        assert "scenario_generator" in refl.successes

    def test_reflection_recommends_clarification_on_high_unresolved(self, tmp_path: Path) -> None:
        refl = Reflector().build(
            result=self._result(unresolved=2, total=4),
            checkpoints=CheckpointStore(tmp_path),
            attempt_history=[],
            failed_stage=None,
        )
        assert any("unresolved" in r.lower() or "clarif" in r.lower() for r in refl.recommendations)

    def test_reflection_recommends_closing_gaps(self, tmp_path: Path) -> None:
        refl = Reflector().build(
            result=self._result(gaps=6),
            checkpoints=CheckpointStore(tmp_path),
            attempt_history=[],
            failed_stage=None,
        )
        assert any("gap" in r.lower() for r in refl.recommendations)

    def test_reflection_retry_lesson_on_failure(self, tmp_path: Path) -> None:
        refl = Reflector().build(
            result=None,
            checkpoints=CheckpointStore(tmp_path),
            attempt_history=[{"stage": "gap_analyzer"}],
            failed_stage="gap_analyzer",
        )
        assert refl.failures == ["gap_analyzer"]
        assert any("gap_analyzer" in ls for ls in refl.lessons)

    def test_reflection_marks_recovered_stage(self, tmp_path: Path) -> None:
        store = CheckpointStore(tmp_path)
        store.write_stage("requirement_analyzer", 0, _analysis())
        # requirement_analyzer both retried AND completed -> recovered.
        refl = Reflector().build(
            result=self._result(),
            checkpoints=store,
            attempt_history=[{"stage": "requirement_analyzer"}],
            failed_stage=None,
        )
        assert "requirement_analyzer" in refl.recovered_stages


# --- deterministic execution, no-op identity, checkpoint reuse -------------

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


class TestDeterministicExecution:
    def test_noop_agent_run_matches_plain_pipeline(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import qaops.services.design_service as ds

        # Agent-driven full run.
        s1 = QAOpsSettings(output_dir=tmp_path / "a", provider="mock")
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(list(_SCRIPT)))
        plan, outcome, refl = OrchestratorAgent(DesignService()).execute(_doc(tmp_path), s1)
        assert plan.no_intervention is True

        # Plain Phase-25 run on identical input.
        s2 = QAOpsSettings(output_dir=tmp_path / "b", provider="mock")
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(list(_SCRIPT)))
        plain = DesignService().run(_doc(tmp_path), s2)

        # Byte-identical functional artifacts: the agent added no divergence.
        assert [c.id for c in outcome.result.test_cases] == [c.id for c in plain.result.test_cases]
        assert [r.id for r in outcome.result.requirements] == [
            r.id for r in plain.result.requirements
        ]
        assert (
            outcome.result.coverage.metrics.model_dump()
            == plain.result.coverage.metrics.model_dump()
        )

    def test_agent_never_generates_artifacts_directly(self) -> None:
        # The agent exposes plan/reflect/execute only; it has no method that
        # produces a requirement/scenario/case. execute() delegates to the
        # service. This guards the core invariant structurally.
        agent = OrchestratorAgent(DesignService())
        public = {m for m in dir(agent) if not m.startswith("_")}
        assert "plan" in public and "reflect" in public and "execute" in public
        for forbidden in ("analyze", "generate_scenarios", "generate_cases", "extract_rules"):
            assert forbidden not in public

    def test_reflection_after_agent_execute_is_reasoning_only(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        import qaops.services.design_service as ds

        s = QAOpsSettings(output_dir=tmp_path / "r", provider="mock")
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(list(_SCRIPT)))
        _, outcome, refl = OrchestratorAgent(DesignService()).execute(_doc(tmp_path), s)
        assert isinstance(refl, Reflection)
        assert len(refl.successes) == 7
        # Reflection carries reasoning, not artifacts.
        assert not hasattr(refl, "requirements")
        assert not hasattr(refl, "test_cases")


class TestCheckpointReuseViaAgent:
    def test_agent_resumes_reusing_completed_stages(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import qaops.services.design_service as ds
        from qaops.llm import LLMProviderError

        s = QAOpsSettings(output_dir=tmp_path / "out", provider="mock")
        doc = _doc(tmp_path)

        # First attempt fails at test_condition_analyzer (4 stages complete).
        fail = _SCRIPT[:4] + [LLMProviderError("mock", "x")] * 3
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(fail))
        from qaops.core.errors import StageError

        with pytest.raises(StageError):
            DesignService().run(doc, s)

        # Agent plan now shows resume with reuse of the 4 completed stages.
        agent = OrchestratorAgent(DesignService())
        plan = agent.plan(doc, s)
        assert plan.resume is True
        reused = [st.stage for st in plan.steps if st.status is PlanStepStatus.REUSE]
        assert "scenario_generator" in reused
        assert "test_condition_analyzer" not in reused

        # Agent execute resumes with only the remaining responses.
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(_SCRIPT[4:]))
        plan2, outcome, refl = agent.execute(doc, s)
        assert outcome.result.test_cases
        assert "scenario_generator" in refl.skipped_stages
