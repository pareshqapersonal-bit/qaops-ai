"""Phase 28 tests: the multi-agent refactor (ADR-043).

This phase is a pure structural refactor, so its primary test is behavioural
identity: the SupervisorAgent and the OrchestratorAgent facade must produce
byte-identical artifacts, checkpoints, and loop summaries, and every existing
Phase 25-27 test must still pass unchanged (that is the identity guarantee).

These tests add explicit assertions for the new structure: the three specialized
agents own their responsibilities, the supervisor coordinates them, the loop
drives acts through the ExecutionAgent, and the facade still works.

All runs use MockLLMClient; no live LLM.
"""

import json
from pathlib import Path

from qaops.agent import (
    ExecutionAgent,
    GoalDrivenLoop,
    OrchestratorAgent,
    PlanningAgent,
    ReflectionAgent,
    SupervisorAgent,
)
from qaops.agent.reflection import Reflector
from qaops.config import QAOpsSettings
from qaops.llm import MockLLMClient
from qaops.services import DesignService

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


def _checkpoints(output_dir: Path) -> list[str]:
    cp = output_dir / "checkpoints"
    return sorted(p.name for p in cp.glob("*.json")) if cp.exists() else []


# --- structure --------------------------------------------------------------


class TestAgentStructure:
    def test_three_specialized_agents_have_names(self) -> None:
        service = DesignService()
        assert PlanningAgent(service).name == "planning"
        assert ExecutionAgent(service).name == "execution"
        assert ReflectionAgent().name == "reflection"

    def test_supervisor_and_facade_names(self) -> None:
        assert SupervisorAgent(DesignService()).name == "supervisor"
        assert OrchestratorAgent(DesignService()).name == "orchestrator"

    def test_execution_agent_only_delegates(self) -> None:
        # ExecutionAgent exposes only run/resume - it cannot execute a stage.
        agent = ExecutionAgent(DesignService())
        public = {m for m in dir(agent) if not m.startswith("_")}
        assert public == {"name", "run", "resume"}

    def test_planning_agent_only_plans(self) -> None:
        agent = PlanningAgent(DesignService())
        public = {m for m in dir(agent) if not m.startswith("_")}
        assert public == {"name", "plan"}

    def test_loop_is_constructed_with_execution_agent(self) -> None:
        # The loop drives acts through an ExecutionAgent, per ADR-043.
        loop = GoalDrivenLoop(ExecutionAgent(DesignService()), Reflector())
        public = {m for m in dir(loop) if not m.startswith("_")}
        assert public == {"run"}


# --- behavioural identity (the primary Phase 28 criterion) ------------------


class TestBehaviouralIdentity:
    def test_supervisor_vs_facade_artifacts_identical(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import qaops.services.design_service as ds

        s1 = QAOpsSettings(output_dir=tmp_path / "sup", provider="mock")
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(list(_SCRIPT)))
        _, out_sup, sum_sup = SupervisorAgent(DesignService()).execute_until_goal(
            _doc(tmp_path), s1
        )

        s2 = QAOpsSettings(output_dir=tmp_path / "orch", provider="mock")
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(list(_SCRIPT)))
        _, out_orch, sum_orch = OrchestratorAgent(DesignService()).execute_until_goal(
            _doc(tmp_path), s2
        )

        assert _snapshot(out_sup.result) == _snapshot(out_orch.result)
        assert _checkpoints(s1.output_dir) == _checkpoints(s2.output_dir)
        assert sum_sup.terminal_reason == sum_orch.terminal_reason
        assert len(sum_sup.iterations) == len(sum_orch.iterations)

    def test_supervisor_vs_direct_service_artifacts_identical(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        import qaops.services.design_service as ds

        s1 = QAOpsSettings(output_dir=tmp_path / "direct", provider="mock")
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(list(_SCRIPT)))
        direct = DesignService().run(_doc(tmp_path), s1)

        s2 = QAOpsSettings(output_dir=tmp_path / "sup", provider="mock")
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(list(_SCRIPT)))
        _, out_sup, _ = SupervisorAgent(DesignService()).execute_until_goal(_doc(tmp_path), s2)

        assert _snapshot(direct.result) == _snapshot(out_sup.result)

    def test_facade_execute_matches_supervisor_execute(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import qaops.services.design_service as ds

        s1 = QAOpsSettings(output_dir=tmp_path / "a", provider="mock")
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(list(_SCRIPT)))
        _, out_a, _ = SupervisorAgent(DesignService()).execute(_doc(tmp_path), s1)

        s2 = QAOpsSettings(output_dir=tmp_path / "b", provider="mock")
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(list(_SCRIPT)))
        _, out_b, _ = OrchestratorAgent(DesignService()).execute(_doc(tmp_path), s2)

        assert _snapshot(out_a.result) == _snapshot(out_b.result)


# --- invariants preserved ---------------------------------------------------


class TestInvariantsPreserved:
    def test_supervisor_generates_no_artifacts(self) -> None:
        agent = SupervisorAgent(DesignService())
        public = {m for m in dir(agent) if not m.startswith("_")}
        for forbidden in ("generate", "analyze", "extract_rules", "write_stage"):
            assert forbidden not in public

    def test_supervisor_only_reads_checkpoints(self, tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        import qaops.services.design_service as ds

        # After a run, the only checkpoints are the pipeline's own stage files.
        s = QAOpsSettings(output_dir=tmp_path / "o", provider="mock")
        monkeypatch.setattr(ds, "create_client", lambda settings: MockLLMClient(list(_SCRIPT)))
        SupervisorAgent(DesignService()).execute_until_goal(_doc(tmp_path), s)
        files = _checkpoints(s.output_dir)
        # 7 stage checkpoints + manifest.json (Phase 25 layout).
        assert "manifest.json" in files
        stage_files = [f for f in files if f != "manifest.json"]
        assert len(stage_files) == 7
