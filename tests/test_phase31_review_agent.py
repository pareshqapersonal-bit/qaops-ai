"""Phase 31 tests: advisory ReviewAgent + ReviewAdvice (ADR-046).

Covers the deterministic roll-up (prioritization, consolidated recommendations,
provenance), the safe LLM fallback and its structural immutability guarantees,
and the gating flag (OFF by default -> byte-identical). Uses the two real run
artifacts' ReviewReports as fixtures.
"""

import json
from pathlib import Path

from qaops.agent.agents import ReviewAgent
from qaops.config import QAOpsSettings
from qaops.models import ReviewReport, TestDesignResult
from qaops.models.enums import ReviewSeverity
from qaops.review import QualityReviewer

_FIXTURES = Path(__file__).parent / "fixtures" / "phase29"


def _report(name: str) -> ReviewReport:
    result = TestDesignResult.model_validate(json.loads((_FIXTURES / name).read_text()))
    return QualityReviewer().review(result)


class _StubClient:
    """Minimal LLM client stub returning a fixed JSON body."""

    def __init__(self, text: str) -> None:
        self._text = text

    def complete(self, request: object) -> object:  # noqa: ARG002
        class _Resp:
            text = self._text

        return _Resp()


class TestDeterministicAdvice:
    def test_advice_is_deterministic(self) -> None:
        report = _report("auto_delete_result.json")
        s = QAOpsSettings()
        first = ReviewAgent().advise(report, s)
        second = ReviewAgent().advise(report, s)
        assert first.model_dump() == second.model_dump()

    def test_generated_by_deterministic_without_client(self) -> None:
        advice = ReviewAgent().advise(_report("bogo_result.json"), QAOpsSettings())
        assert advice.generated_by == "deterministic"

    def test_items_prioritized_critical_first(self) -> None:
        advice = ReviewAgent().advise(_report("auto_delete_result.json"), QAOpsSettings())
        ranks = {ReviewSeverity.CRITICAL: 0, ReviewSeverity.WARNING: 1, ReviewSeverity.INFO: 2}
        seq = [ranks[i.severity] for i in advice.items]
        assert seq == sorted(seq)  # non-decreasing severity rank

    def test_items_echo_findings_exactly(self) -> None:
        report = _report("auto_delete_result.json")
        advice = ReviewAgent().advise(report, QAOpsSettings())
        # Same set of codes, and severities/references echoed unchanged.
        by_code = {f.code: f for f in report.findings}
        assert {i.code for i in advice.items} == set(by_code)
        for item in advice.items:
            assert item.severity is by_code[item.code].severity
            assert item.references == by_code[item.code].references

    def test_recommendations_deduplicated(self) -> None:
        advice = ReviewAgent().advise(_report("auto_delete_result.json"), QAOpsSettings())
        assert len(advice.recommendations) == len(set(advice.recommendations))

    def test_headline_reflects_severity_counts(self) -> None:
        advice = ReviewAgent().advise(_report("auto_delete_result.json"), QAOpsSettings())
        assert "critical" in advice.headline.lower()


class TestLLMEnrichment:
    def _settings_with_llm(self) -> QAOpsSettings:
        return QAOpsSettings(review_advice_enabled=True)

    def test_llm_refines_prose_and_sets_provenance(self) -> None:
        from qaops.llm import PromptLoader

        report = _report("bogo_result.json")
        codes = [f.code for f in report.findings]
        body = json.dumps(
            {
                "headline": "Refined headline.",
                "items": [{"code": codes[0], "explanation": "Refined explanation."}],
            }
        )
        agent = ReviewAgent(
            client=_StubClient(body), prompts=PromptLoader(), settings=self._settings_with_llm()
        )
        advice = agent.advise(report, self._settings_with_llm())
        assert advice.generated_by == "llm"
        assert advice.headline == "Refined headline."
        refined = next(i for i in advice.items if i.code == codes[0])
        assert refined.explanation == "Refined explanation."

    def test_llm_cannot_add_unknown_codes(self) -> None:
        from qaops.llm import PromptLoader

        report = _report("bogo_result.json")
        body = json.dumps(
            {
                "headline": "H",
                "items": [{"code": "not_a_real_code", "explanation": "should be ignored"}],
            }
        )
        agent = ReviewAgent(
            client=_StubClient(body), prompts=PromptLoader(), settings=self._settings_with_llm()
        )
        advice = agent.advise(report, self._settings_with_llm())
        assert all(i.code != "not_a_real_code" for i in advice.items)

    def test_llm_cannot_change_severity_or_references(self) -> None:
        from qaops.llm import PromptLoader

        report = _report("auto_delete_result.json")
        by_code = {f.code: f for f in report.findings}
        code = report.findings[0].code
        # Malicious body trying to flip severity/references is ignored structurally.
        body = json.dumps(
            {"items": [{"code": code, "explanation": "x", "severity": "info", "references": ["Z"]}]}
        )
        agent = ReviewAgent(
            client=_StubClient(body), prompts=PromptLoader(), settings=self._settings_with_llm()
        )
        advice = agent.advise(report, self._settings_with_llm())
        item = next(i for i in advice.items if i.code == code)
        assert item.severity is by_code[code].severity
        assert item.references == by_code[code].references

    def test_llm_failure_falls_back_to_deterministic(self) -> None:
        from qaops.llm import PromptLoader

        report = _report("bogo_result.json")
        agent = ReviewAgent(
            client=_StubClient("not json at all"),
            prompts=PromptLoader(),
            settings=self._settings_with_llm(),
        )
        advice = agent.advise(report, self._settings_with_llm())
        assert advice.generated_by == "deterministic"
        assert advice.items  # still complete


class TestReviewReportUnchanged:
    def test_advise_does_not_mutate_report(self) -> None:
        report = _report("auto_delete_result.json")
        before = report.model_dump()
        ReviewAgent().advise(report, QAOpsSettings())
        assert report.model_dump() == before
