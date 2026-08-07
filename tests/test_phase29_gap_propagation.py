"""Phase 29 tests: narrow gap propagation via the analyzer prompt (ADR-044).

The failing Auto-Delete run classified 20/22 conditions unresolved because the
model applied the rule "any gap on the condition's requirement -> unresolved",
fanning each requirement-level gap across every condition on that requirement -
including conditions that verify a different, fully-specified aspect.

Phase 29 fixes this in the TestConditionAnalyzer PROMPT only: a gap makes a
condition unresolved only when the gap's missing information is the very thing
that condition verifies. The deterministic backstop (`_apply_gap_linkage`) is
intentionally left unchanged - the production failure originated in the LLM's
classification, and we change the smallest surface supported by evidence.

Because the fix is a prompt-reasoning change, its effect is validated on the live
LLM by re-running the PRD; these tests pin the prompt contract (the instruction
is present and coherent) and keep the two real run artifacts as fixtures for that
re-run comparison.
"""

import json
from pathlib import Path

from qaops.llm import PromptLoader

_FIXTURES = Path(__file__).parent / "fixtures" / "phase29"


class TestPromptContract:
    """Pin the narrowing instruction and its guard rail in the prompt."""

    def _rendered(self) -> str:
        return PromptLoader().render(
            "test_condition_analyzer",
            scenarios_json="[]",
            requirements_json="[]",
            rules_json="[]",
            gaps_json="[]",
        )

    def test_prompt_instructs_narrow_gap_application(self) -> None:
        rendered = self._rendered()
        assert "apply each gap NARROWLY" in rendered
        assert "Sharing a requirement with a gap is NOT sufficient" in rendered

    def test_prompt_keeps_the_anti_ignore_guard_rail(self) -> None:
        # Narrowing must not become ignoring or fabricating.
        rendered = self._rendered()
        assert "do NOT ignore a gap that genuinely applies" in rendered

    def test_prompt_preserves_never_fabricate(self) -> None:
        rendered = self._rendered()
        assert "Do NOT invent an expected result" in rendered

    def test_prompt_still_evidence_first(self) -> None:
        rendered = self._rendered()
        assert "EVIDENCE-FIRST" in rendered


class TestRealArtifactFixtures:
    """The two real run artifacts are retained for live-LLM re-run comparison.

    They are not asserted against the deterministic layer (unchanged in this
    phase); they document the failing baseline (Auto-Delete 20/22 unresolved) and
    the healthy baseline (BOGO 4/11) so the prompt fix can be validated by
    re-running the pipeline and comparing.
    """

    def test_auto_delete_fixture_records_the_failure_baseline(self) -> None:
        d = json.loads((_FIXTURES / "auto_delete_result.json").read_text())
        m = d["coverage"]["metrics"]
        assert m["total_conditions"] == 22
        assert m["unresolved_conditions"] == 20  # the production failure

    def test_bogo_fixture_records_the_healthy_baseline(self) -> None:
        d = json.loads((_FIXTURES / "bogo_result.json").read_text())
        m = d["coverage"]["metrics"]
        assert m["total_conditions"] == 11
        assert m["unresolved_conditions"] == 4  # correct, must not regress
