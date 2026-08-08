"""ReviewAgent - advisory narrative over a ReviewReport (ADR-046, Phase 31).

The ReviewAgent CONSUMES the deterministic ReviewReport produced by the Phase 30
QualityReviewer and produces advisory `ReviewAdvice`: prioritized, plain-language
explanations of the findings plus consolidated recommendations for a QA Lead
reviewing artifacts before client handoff.

Hard boundaries (enforced by construction):
  * it reads a ReviewReport and NOTHING else - no TestDesignResult, no metrics -
    so it cannot recompute anything;
  * it never creates findings, changes a finding's severity or references,
    mutates artifacts, affects execution, affects loop decisions, or feeds advice
    back into generation;
  * the ReviewReport remains authoritative.

Deterministic by default: `advise()` always builds a complete ReviewAdvice from
the report alone. An optional LLM pass may refine ONLY the free-text prose
(headline and per-item explanation) and is best-effort - any failure or unusable
output falls back to the deterministic advice. Provenance is recorded in
`generated_by` ("deterministic" | "llm").
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from qaops.agent.base import Agent
from qaops.models import ReviewAdvice, ReviewAdviceItem
from qaops.models.enums import ReviewSeverity

if TYPE_CHECKING:
    from qaops.config import QAOpsSettings
    from qaops.llm import LLMClient, PromptLoader
    from qaops.models import ReviewReport

# Severity ordering for prioritization (critical first). Stable, deterministic.
_SEVERITY_RANK: dict[ReviewSeverity, int] = {
    ReviewSeverity.CRITICAL: 0,
    ReviewSeverity.WARNING: 1,
    ReviewSeverity.INFO: 2,
}

_REVIEW_SYSTEM = (
    "You are a QA review assistant. You are given a fixed list of deterministic "
    "quality findings about a generated test-design pack. You EXPLAIN and PRIORITISE "
    "them for a QA lead in plain language. You MUST NOT invent findings, change any "
    "finding's severity or references, or produce requirements, business rules, gaps, "
    "scenarios, conditions, test cases, or coverage. Respond ONLY with JSON of the "
    'form {"headline":"<one line>","items":[{"code":"<finding code>",'
    '"explanation":"<plain-language explanation>"}, ...]}. Use only codes present in '
    "the input; any other code is ignored."
)


class ReviewAgent(Agent):
    """Advisory ReviewAgent: ReviewReport -> ReviewAdvice (never authors findings)."""

    def __init__(
        self,
        *,
        client: LLMClient | None = None,
        prompts: PromptLoader | None = None,
        settings: QAOpsSettings | None = None,
    ) -> None:
        self._client = client
        self._prompts = prompts
        self._settings = settings

    @property
    def name(self) -> str:
        return "review"

    def advise(self, report: ReviewReport, settings: QAOpsSettings) -> ReviewAdvice:
        """Build advisory ReviewAdvice from a ReviewReport. Never mutates it."""
        advice = self._deterministic_advice(report)
        # Optional LLM prose refinement - best-effort, prose only, never structural.
        if self._client is not None and self._prompts is not None and self._settings is not None:
            self._enrich(advice, report)
        return advice

    # -- deterministic roll-up (always runs) --------------------------------

    def _deterministic_advice(self, report: ReviewReport) -> ReviewAdvice:
        ordered = sorted(
            report.findings,
            key=lambda f: (_SEVERITY_RANK.get(f.severity, 99), f.category.value, f.code),
        )
        items = [
            ReviewAdviceItem(
                code=f.code,
                severity=f.severity,
                explanation=f.message,
                references=list(f.references),
            )
            for f in ordered
        ]
        return ReviewAdvice(
            source_name=report.source_name,
            headline=self._headline(report),
            items=items,
            recommendations=self._consolidated_recommendations(report),
            generated_by="deterministic",
        )

    @staticmethod
    def _headline(report: ReviewReport) -> str:
        crit = sum(1 for f in report.findings if f.severity is ReviewSeverity.CRITICAL)
        warn = sum(1 for f in report.findings if f.severity is ReviewSeverity.WARNING)
        if crit:
            return (
                f"{crit} critical and {warn} warning finding(s) - not ready for client "
                "handoff without resolution."
            )
        if warn:
            return f"{warn} warning finding(s) - review before client handoff."
        return "No blocking findings - the pack is ready for review."

    @staticmethod
    def _consolidated_recommendations(report: ReviewReport) -> list[str]:
        # Severity-priority order over findings' recommendations, then the report's
        # own recommendations; de-duplicated in first-seen order. Deterministic.
        ordered = sorted(
            report.findings, key=lambda f: (_SEVERITY_RANK.get(f.severity, 99), f.code)
        )
        seen: set[str] = set()
        out: list[str] = []
        for f in ordered:
            rec = f.recommendation.strip()
            if rec and rec not in seen:
                seen.add(rec)
                out.append(rec)
        for rec in report.recommendations:
            r = rec.strip()
            if r and r not in seen:
                seen.add(r)
                out.append(r)
        return out

    # -- optional LLM prose enrichment (best-effort, prose only) ------------

    def _enrich(self, advice: ReviewAdvice, report: ReviewReport) -> None:
        assert self._client is not None and self._prompts is not None
        assert self._settings is not None
        try:
            from qaops.llm import LLMMessage, LLMRequest

            findings_json = json.dumps(
                [
                    {
                        "code": f.code,
                        "severity": f.severity.value,
                        "category": f.category.value,
                        "message": f.message,
                        "references": list(f.references),
                    }
                    for f in report.findings
                ]
            )
            rendered = self._prompts.render(
                "agent_review_advice",
                findings_json=findings_json,
                recommendations_json=json.dumps(list(report.recommendations)),
            )
            request = LLMRequest(
                system=_REVIEW_SYSTEM,
                messages=[LLMMessage(role="user", content=rendered)],
                temperature=self._settings.temperature,
                max_output_tokens=self._settings.max_output_tokens,
            )
            response = self._client.complete(request)
            data = json.loads(response.text)
        except Exception:  # noqa: BLE001 - enrichment is best-effort
            return
        if not isinstance(data, dict):
            return

        # Refine ONLY prose. Structural fields (code/severity/references, the set of
        # items, and recommendations) are never changed by the LLM.
        refined = False
        headline = data.get("headline")
        if isinstance(headline, str) and headline.strip():
            advice.headline = headline.strip()
            refined = True

        explanations = data.get("items")
        if isinstance(explanations, list):
            by_code = {
                str(e.get("code")): str(e.get("explanation", "")).strip()
                for e in explanations
                if isinstance(e, dict) and e.get("code")
            }
            for item in advice.items:  # only existing items; unknown codes ignored
                new_text = by_code.get(item.code)
                if new_text:
                    item.explanation = new_text
                    refined = True

        if refined:
            advice.generated_by = "llm"
