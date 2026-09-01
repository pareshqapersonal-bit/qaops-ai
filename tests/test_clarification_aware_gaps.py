"""Regression tests for clarification-aware gap analysis (prompt-level fix).

The fix teaches the gap-analyzer prompt that a requirement's "assumptions" may hold
authoritative answered clarifications that close gaps. These tests verify the
DETERMINISTIC, non-LLM parts of that contract:
  - the clarification-awareness instruction is present and renders in the prompt,
  - a requirement's non-empty assumptions (where accepted clarifications live) are
    serialized into the actual prompt sent to the gap analyzer,
  - multiple accumulated clarifications all reach the prompt.

The SEMANTIC decision (does the model actually suppress a covered gap / keep an
unrelated one) depends on the live LLM and is NOT asserted here - the suite is
mock-based. Where a scenario's outcome is model-judgment, the test asserts the
prompt CARRIES the information needed for that judgment, not the judgment itself.
"""

import pytest

from qaops.config import QAOpsSettings
from qaops.llm import MockLLMClient, PromptLoader
from qaops.models import RequirementAnalysisResult
from qaops.models.domain import Requirement
from qaops.pipelines.test_design._support import requirements_as_prompt_json
from qaops.pipelines.test_design.gaps import GapAnalyzer

_NO_GAPS = '{"gaps": []}'


@pytest.fixture
def settings() -> QAOpsSettings:
    return QAOpsSettings()


@pytest.fixture
def prompts() -> PromptLoader:
    return PromptLoader()


def _req(rid: str, desc: str, assumptions: list[str] | None = None) -> Requirement:
    return Requirement(
        id=rid,
        title=desc[:40],
        description=desc,
        assumptions=list(assumptions or []),
    )


def _run_gap(mock: MockLLMClient, prompts: PromptLoader, settings: QAOpsSettings, reqs):
    data = RequirementAnalysisResult(
        source_name="src",
        source_text="System shall lock the account after repeated failed login attempts.",
        requirements=list(reqs),
    )
    GapAnalyzer(mock, prompts, settings).run(data)
    return mock.requests[0].messages[0].content


# =====================================================================
# Prompt-content: the clarification-awareness instruction exists
# =====================================================================


class TestPromptSemantics:
    def _prompt(self) -> str:
        return PromptLoader().render("gap_analyzer", requirements_json="[]", source_text="x")

    def test_instruction_present(self) -> None:
        p = self._prompt().casefold()
        assert "assumptions" in p
        assert "clarification" in p
        assert "authoritative" in p

    def test_instruction_is_conditional_not_blanket(self) -> None:
        # Must NOT tell the model "all assumptions resolve gaps"; the closing is
        # conditional on the assumption actually supplying the missing information.
        p = self._prompt().casefold()
        assert "only when" in p or "only when its content" in p
        assert "genuinely unresolved" in p  # keeps reporting real gaps

    def test_prompt_still_renders_without_placeholders(self) -> None:
        assert "$" not in self._prompt()


# =====================================================================
# Test 5 - clarification reaches the actual prompt
# =====================================================================


class TestClarificationReachesPrompt:
    def test_nonempty_assumptions_serialized_into_prompt(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        req = _req(
            "REQ-001",
            "System shall lock the account after repeated failed login attempts.",
            ["Clarification (max attempts before lockout?) -> 5 failed attempts"],
        )
        content = _run_gap(MockLLMClient([_NO_GAPS]), prompts, settings, [req])
        assert "assumptions" in content
        assert "5 failed attempts" in content  # the answer text is present

    def test_serialization_includes_clarification_text(self) -> None:
        req = _req("REQ-001", "Lock account", ["Clarification (limit?) -> 5 attempts"])
        out = requirements_as_prompt_json([req])
        assert "assumptions" in out
        assert "5 attempts" in out

    def test_empty_assumptions_not_serialized(self) -> None:
        # A requirement with no clarifications must not emit an assumptions field
        # (exclude_defaults keeps the prompt lean); only accepted clarifications appear.
        req = _req("REQ-001", "Lock account")
        assert "assumptions" not in requirements_as_prompt_json([req])


# =====================================================================
# Test 1/2/3 - the information needed for the decision reaches the prompt
# (semantic outcome is the live model's; we assert the carrier, not the verdict)
# =====================================================================


class TestClarificationContentCarried:
    def test_direct_clarification_answer_in_prompt(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        # Test 1 carrier: the answer that should CLOSE the gap is in the prompt.
        req = _req(
            "REQ-001",
            "System shall lock the account after repeated failed login attempts.",
            ["Clarification (max failed attempts before lockout?) -> Account is locked after 5"],
        )
        content = _run_gap(MockLLMClient([_NO_GAPS]), prompts, settings, [req])
        assert "locked after 5" in content

    def test_unrelated_clarification_also_carried_verbatim(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        # Test 2 carrier: an UNRELATED clarification is present but the prompt's
        # conditional instruction (asserted above) is what keeps the real gap open.
        req = _req(
            "REQ-001",
            "System shall lock the account after repeated failed login attempts.",
            ["Clarification (lockout duration?) -> Locked accounts remain locked for 30 minutes"],
        )
        content = _run_gap(MockLLMClient([_NO_GAPS]), prompts, settings, [req])
        assert "30 minutes" in content
        # The unrelated answer does not mention the max-attempts value.
        assert "5 failed" not in content


# =====================================================================
# Test 4 - multiple accumulated clarifications all reach the prompt
# =====================================================================


class TestMultipleClarifications:
    def test_two_requirements_both_clarifications_in_prompt(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        reqs = [
            _req(
                "REQ-001", "Lock account after failed logins", ["Clarification (A?) -> 5 attempts"]
            ),
            _req("REQ-002", "Notify user on lockout", ["Clarification (B?) -> email notification"]),
        ]
        content = _run_gap(MockLLMClient([_NO_GAPS]), prompts, settings, reqs)
        assert "5 attempts" in content  # gap A answer
        assert "email notification" in content  # gap B answer

    def test_multiple_assumptions_on_one_requirement_all_serialized(self) -> None:
        req = _req(
            "REQ-001",
            "Lock account",
            [
                "Clarification (A?) -> 5 attempts",
                "Clarification (B?) -> 30 minute lockout",
            ],
        )
        out = requirements_as_prompt_json([req])
        assert "5 attempts" in out
        assert "30 minute lockout" in out
