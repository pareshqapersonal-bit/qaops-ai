"""Phase QA-Manual-1: prompt-content contract tests.

These protect the manual-QA refinement of the gap-analyzer and clarification-agent
prompts WITHOUT asserting exact LLM-generated wording (the runtime behavior is
mock-based elsewhere). They verify that the prompts (a) still render with exactly
their existing placeholders, (b) keep their JSON output contract, (c) carry the
manual-QA coverage categories, (d) carry the implementation-question guardrail, and
(e) keep the yes/no-first answer-type strategy.
"""

from qaops.llm.prompt_loader import PromptLoader


def _gap_prompt() -> str:
    return PromptLoader().render("gap_analyzer", requirements_json="[]", source_text="x")


def _agent_prompt() -> str:
    return PromptLoader().render("clarification_agent", requirements_json="[]", gaps_json="[]")


class TestPromptsRenderAndContract:
    def test_gap_prompt_renders_without_unresolved_placeholders(self) -> None:
        assert "$" not in _gap_prompt()

    def test_agent_prompt_renders_without_unresolved_placeholders(self) -> None:
        assert "$" not in _agent_prompt()

    def test_gap_json_contract_unchanged(self) -> None:
        p = _gap_prompt()
        tokens = (
            '"gaps"',
            '"description"',
            '"severity"',
            '"requirement_id"',
            '"suggested_question"',
        )
        for token in tokens:
            assert token in p, token
        # Severity vocabulary unchanged.
        assert "blocker" in p and "major" in p and "minor" in p

    def test_agent_json_contract_unchanged(self) -> None:
        p = _agent_prompt()
        tokens = ('"questions"', '"gap_index"', '"skip"', '"answer_type"', '"options"', '"reason"')
        for token in tokens:
            assert token in p, token


class TestManualQaCategories:
    def test_gap_prompt_frames_manual_qa_analyst(self) -> None:
        p = _gap_prompt().lower()
        assert "manual qa" in p

    def test_gap_prompt_covers_qa_categories(self) -> None:
        p = _gap_prompt().lower()
        # Behavioral coverage areas a manual tester needs.
        for topic in ("empty", "loading", "error state", "navigation", "responsive", "mobile"):
            assert topic in p, topic
        # Data-state coverage.
        assert "zero records" in p or "boundary" in p

    def test_gap_prompt_keeps_materiality_rule(self) -> None:
        p = _gap_prompt().lower()
        assert "empty list is a valid" in p
        assert "materially" in p


class TestImplementationGuardrail:
    def test_gap_prompt_forbids_implementation_details(self) -> None:
        p = _gap_prompt().lower()
        for term in ("css selector", "data-testid", "z-index", "automation locator"):
            assert term in p, term

    def test_agent_prompt_forbids_implementation_details(self) -> None:
        p = _agent_prompt().lower()
        for term in ("css selector", "data-testid", "z-index", "automation locator"):
            assert term in p, term

    def test_agent_prompt_prefers_design_reference(self) -> None:
        assert "approved design" in _agent_prompt().lower()


class TestYesNoFirstStrategyIntact:
    def test_answer_type_preference_order_present(self) -> None:
        p = _agent_prompt()
        # Ordered preference still documented, boolean first, text last.
        b = p.index("boolean")
        ss = p.index("single_select")
        txt = p.rindex("text")
        assert b < ss < txt
        assert "least-typing" in p.lower() or "as little typing" in p.lower()

    def test_all_answer_types_still_offered(self) -> None:
        p = _agent_prompt()
        for t in ("boolean", "single_select", "multi_select", "numeric", "date", "text"):
            assert t in p, t
