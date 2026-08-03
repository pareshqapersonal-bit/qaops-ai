"""Execution planning for the Orchestrator Agent (ADR-041, Phase 26).

The planner answers "how should the pipeline execute?" - which stages run, which
are reused from checkpoints, and why - WITHOUT running anything and WITHOUT
generating any pipeline artifact.

Two layers, deliberately separated so determinism is never at the LLM's mercy:

  * STRUCTURE (deterministic): the ordered stage list comes from the entry point
    via stage_names_for; reuse-vs-run per stage comes from CheckpointStore;
    resume-vs-restart and the recorded Decisions are computed from checkpoint
    state. This layer alone is a complete, correct plan.
  * REASONING (optional LLM): human-readable per-step `reason`/`expected_output`
    and the decision prose may be enriched by the LLM for clarity. If the LLM is
    absent or unusable, deterministic defaults are used. The LLM never changes
    WHICH stages run - only how the plan reads.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from qaops.agent.models import (
    Decision,
    ExecutionPlan,
    PlanStep,
    PlanStepStatus,
)
from qaops.entrypoints import stage_names_for

if TYPE_CHECKING:
    from qaops.config import QAOpsSettings
    from qaops.entrypoints.entry_point import EntryPoint
    from qaops.execution.checkpoint import CheckpointStore
    from qaops.llm import LLMClient, PromptLoader


# Deterministic, evidence-based descriptions of what each stage produces and
# depends on. Used as the plan's baseline reasoning and as grounding facts the
# LLM must not contradict. These describe EXECUTION, not artifact content.
_STAGE_FACTS: dict[str, tuple[str, str]] = {
    # stage: (depends_on_prev?, expected_output)
    "requirement_analyzer": ("the source document", "structured requirements"),
    "business_rule_extractor": ("analyzed requirements", "business rules per requirement"),
    "gap_analyzer": ("requirements and rules", "a gap report of missing/ambiguous specs"),
    "scenario_generator": ("requirements, rules, gaps", "test scenarios linked to requirements"),
    "test_condition_analyzer": ("scenarios", "evidence-bound test conditions"),
    "test_case_generator": ("test conditions + expansion plan", "expanded manual test cases"),
    "coverage_validator": ("all prior artifacts", "coverage metrics and traceability"),
}


class ExecutionPlanner:
    """Builds an ExecutionPlan from the entry point and checkpoint state.

    The structural plan is always deterministic. An optional LLM client enriches
    the prose; when it is None or fails, deterministic reasoning is used.
    """

    def __init__(
        self,
        client: LLMClient | None = None,
        prompts: PromptLoader | None = None,
        settings: QAOpsSettings | None = None,
    ) -> None:
        self._client = client
        self._prompts = prompts
        self._settings = settings

    def build(
        self,
        goal: str,
        entry_point: EntryPoint,
        checkpoints: CheckpointStore | None = None,
    ) -> ExecutionPlan:
        stage_names = stage_names_for(entry_point)
        completed = set(checkpoints.completed_stages()) if checkpoints is not None else set()

        steps: list[PlanStep] = []
        for i, name in enumerate(stage_names):
            depends, output = _STAGE_FACTS.get(name, ("prior stages", "stage output"))
            reuse = name in completed
            steps.append(
                PlanStep(
                    order=i + 1,
                    stage=name,
                    status=PlanStepStatus.REUSE if reuse else PlanStepStatus.RUN,
                    reason=(
                        f"Reuse the checkpointed result of {name}; it completed in a prior attempt."
                        if reuse
                        else f"Run {name}: it derives {output} from {depends}."
                    ),
                    dependencies=[stage_names[i - 1]] if i > 0 else [],
                    expected_output=output,
                )
            )

        resume = bool(completed)
        decisions = self._decisions(resume, completed, stage_names)
        no_intervention = not resume

        plan = ExecutionPlan(
            goal=goal,
            entry_point=entry_point.value,
            resume=resume,
            steps=steps,
            decisions=decisions,
            no_intervention=no_intervention,
        )
        # Optional LLM enrichment of the human-readable reasoning only.
        if self._client is not None and self._prompts is not None and self._settings is not None:
            self._enrich(plan, goal)
        return plan

    def _decisions(
        self, resume: bool, completed: set[str], stage_names: list[str]
    ) -> list[Decision]:
        decisions: list[Decision] = []
        if resume:
            first_to_run = next((n for n in stage_names if n not in completed), None)
            decisions.append(
                Decision(
                    decision=(
                        f"Resume from {first_to_run}"
                        if first_to_run
                        else "Re-export from checkpoints"
                    ),
                    reason="Completed checkpoints exist for earlier stages.",
                    alternative_considered="Restart from the beginning.",
                    rejected_because=(
                        "Restarting would re-run stages that already completed, "
                        "wasting provider calls and time."
                    ),
                )
            )
        else:
            decisions.append(
                Decision(
                    decision="Run the full pipeline from the first stage.",
                    reason="No checkpoints exist for this run.",
                    alternative_considered="Resume from a checkpoint.",
                    rejected_because="There is no completed stage to resume from.",
                )
            )
        return decisions

    def _enrich(self, plan: ExecutionPlan, goal: str) -> None:
        """Best-effort LLM enrichment of per-step reasoning prose.

        Grounded and constrained: the model may only rewrite reason /
        expected_output strings for clarity. It is given the fixed stage list and
        told it MUST NOT add, remove, or reorder stages and MUST NOT produce any
        requirement/scenario/test content. On any failure we keep the
        deterministic plan unchanged.
        """
        assert self._client is not None and self._prompts is not None
        assert self._settings is not None
        try:
            from qaops.llm import LLMMessage, LLMRequest

            stage_list = json.dumps([s.stage for s in plan.steps])
            rendered = self._prompts.render(
                "agent_execution_plan",
                goal=goal,
                entry_point=plan.entry_point,
                stages_json=stage_list,
            )
            request = LLMRequest(
                system=_PLAN_SYSTEM,
                messages=[LLMMessage(role="user", content=rendered)],
                temperature=self._settings.temperature,
                max_output_tokens=self._settings.max_output_tokens,
            )
            response = self._client.complete(request)
            enriched = json.loads(response.text)
        except Exception:  # noqa: BLE001 - enrichment is best-effort, never fatal
            return
        # Apply only to matching stages; ignore anything unexpected.
        by_stage = {s.stage: s for s in plan.steps}
        items = enriched.get("steps", []) if isinstance(enriched, dict) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            step = by_stage.get(str(item.get("stage", "")))
            if step is None:
                continue
            reason = item.get("reason")
            if isinstance(reason, str) and reason.strip():
                step.reason = reason.strip()


_PLAN_SYSTEM = (
    "You are an execution-planning assistant for a QA test-design pipeline. "
    "You explain WHY each pipeline stage runs. You MUST NOT add, remove, or "
    "reorder stages - the stage list is fixed and authoritative. You MUST NOT "
    "produce any requirements, business rules, gaps, scenarios, test "
    "conditions, test cases, or coverage - those are produced by the pipeline, "
    "never by you. Respond ONLY with JSON of the form "
    '{"steps":[{"stage":"<name>","reason":"<why it runs>"}]}.'
)
