"""Post-execution reflection for the Orchestrator Agent (ADR-041, Phase 26).

Reflection is REASONING ONLY. It reads the produced result and execution
metadata (checkpoint manifest, attempt history, coverage metrics) and reports on
how the run went. It NEVER regenerates or mutates any pipeline artifact.

As with planning, the structure is deterministic (successes/failures/retries/
recovered/skipped are computed from the manifest and attempt history; the
clarification recommendation is driven by the deterministic ambiguity signal -
unresolved conditions and gap count). An optional LLM composes the human-
readable narrative summary and lessons; if unavailable, deterministic text is
used.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from qaops.agent.models import Reflection, StageOutcome

if TYPE_CHECKING:
    from qaops.config import QAOpsSettings
    from qaops.execution.checkpoint import CheckpointStore
    from qaops.llm import LLMClient, PromptLoader
    from qaops.models import TestDesignResult

# Recommend clarification when unresolved conditions reach this fraction of all
# conditions, or when this many gaps are open. Deterministic, tunable defaults.
_UNRESOLVED_FRACTION_THRESHOLD = 0.30
_GAP_COUNT_THRESHOLD = 5


class Reflector:
    """Builds a Reflection from execution metadata and the produced result."""

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
        *,
        result: TestDesignResult | None,
        checkpoints: CheckpointStore | None,
        attempt_history: list[dict[str, object]] | None,
        failed_stage: str | None,
        skipped_stages: list[str] | None = None,
    ) -> Reflection:
        manifest = checkpoints.manifest() if checkpoints is not None else []
        attempts = attempt_history or []
        skipped = list(skipped_stages or [])

        successes = [
            str(m["stage_name"])
            for m in manifest
            if m.get("status") == "completed" and "stage_name" in m
        ]
        failures = [failed_stage] if failed_stage else []
        # A stage appears in attempt_history when it needed >1 try; recovered
        # means it retried AND ultimately completed.
        retried_stages = sorted({str(a.get("stage", "")) for a in attempts if a.get("stage")})
        recovered = [s for s in retried_stages if s in successes]

        outcomes = [
            StageOutcome(
                stage=s,
                status="completed",
                retried=s in retried_stages,
                recovered=s in recovered,
            )
            for s in successes
        ]
        for s in skipped:
            outcomes.append(StageOutcome(stage=s, status="skipped", skipped=True))
        if failed_stage:
            outcomes.append(
                StageOutcome(
                    stage=failed_stage,
                    status="failed",
                    retried=failed_stage in retried_stages,
                )
            )

        recommendations = self._recommendations(result)
        lessons = self._lessons(retried_stages, failed_stage, recovered)

        summary = self._summary_text(successes, failures, recovered, skipped, failed_stage)

        reflection = Reflection(
            summary=summary,
            successes=successes,
            failures=[f for f in failures if f],
            retries=retried_stages,
            recovered_stages=recovered,
            skipped_stages=skipped,
            lessons=lessons,
            recommendations=recommendations,
            stage_outcomes=outcomes,
        )
        if self._client is not None and self._prompts is not None and self._settings is not None:
            self._enrich(reflection)
        return reflection

    def _recommendations(self, result: TestDesignResult | None) -> list[str]:
        recs: list[str] = []
        if result is None:
            recs.append(
                "The run did not complete. Review the failed stage's error and "
                "resume once the underlying cause (often provider capacity) clears."
            )
            return recs
        metrics = result.coverage.metrics
        total = metrics.total_conditions
        unresolved = metrics.unresolved_conditions
        if total > 0 and unresolved / total >= _UNRESOLVED_FRACTION_THRESHOLD:
            recs.append(
                f"{unresolved} of {total} conditions are unresolved. Recommend "
                "clarifying the ambiguous requirements before relying on this pack."
            )
        gap_count = len(result.gap_report.gaps)
        if gap_count >= _GAP_COUNT_THRESHOLD:
            recs.append(
                f"{gap_count} specification gaps were reported. Recommend closing "
                "the highest-severity gaps to improve coverage confidence."
            )
        if not recs:
            recs.append("No blocking ambiguity detected; the pack is ready for review.")
        return recs

    def _lessons(
        self, retried: list[str], failed_stage: str | None, recovered: list[str]
    ) -> list[str]:
        lessons: list[str] = []
        if recovered:
            lessons.append(
                f"Stage(s) {', '.join(recovered)} recovered after retry - provider "
                "failover absorbed transient errors."
            )
        if failed_stage:
            lessons.append(
                f"Stage {failed_stage} exhausted its recovery budget; repeated "
                "resumes at the same stage are unlikely to help until the cause clears."
            )
        if not lessons:
            lessons.append("Execution was clean; no retries or failures to learn from.")
        return lessons

    def _summary_text(
        self,
        successes: list[str],
        failures: list[str],
        recovered: list[str],
        skipped: list[str],
        failed_stage: str | None,
    ) -> str:
        if failed_stage:
            return f"Run stopped at {failed_stage} after completing {len(successes)} stage(s)."
        parts = [f"Completed {len(successes)} stage(s)"]
        if skipped:
            parts.append(f"reused {len(skipped)} from checkpoints")
        if recovered:
            parts.append(f"recovered {len(recovered)} after retry")
        return "; ".join(parts) + "."

    def _enrich(self, reflection: Reflection) -> None:
        """Best-effort LLM narrative for summary/lessons. Never fatal, never
        regenerates artifacts - it only rewrites reasoning prose."""
        assert self._client is not None and self._prompts is not None
        assert self._settings is not None
        try:
            from qaops.llm import LLMMessage, LLMRequest

            rendered = self._prompts.render(
                "agent_execution_reflection",
                successes_json=json.dumps(reflection.successes),
                failures_json=json.dumps(reflection.failures),
                retries_json=json.dumps(reflection.retries),
                recovered_json=json.dumps(reflection.recovered_stages),
                skipped_json=json.dumps(reflection.skipped_stages),
            )
            request = LLMRequest(
                system=_REFLECT_SYSTEM,
                messages=[LLMMessage(role="user", content=rendered)],
                temperature=self._settings.temperature,
                max_output_tokens=self._settings.max_output_tokens,
            )
            response = self._client.complete(request)
            data = json.loads(response.text)
        except Exception:  # noqa: BLE001 - enrichment is best-effort
            return
        if isinstance(data, dict):
            summary = data.get("summary")
            if isinstance(summary, str) and summary.strip():
                reflection.summary = summary.strip()
            lessons = data.get("lessons")
            if isinstance(lessons, list):
                cleaned = [str(x).strip() for x in lessons if str(x).strip()]
                if cleaned:
                    reflection.lessons = cleaned


_REFLECT_SYSTEM = (
    "You are an execution-reflection assistant for a QA test-design pipeline. "
    "You summarize HOW a run executed - which stages succeeded, failed, "
    "retried, recovered, or were skipped - and what to do next. You MUST NOT "
    "produce or modify any requirements, business rules, gaps, scenarios, test "
    "conditions, test cases, or coverage. Respond ONLY with JSON of the form "
    '{"summary":"<one paragraph>","lessons":["<lesson>", ...]}.'
)
