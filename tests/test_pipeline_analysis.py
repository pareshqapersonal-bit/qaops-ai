"""Phase 2 tests: requirement-analysis stages against MockLLMClient.

Assertions target the deterministic responsibilities of stage code -
ID assignment, reference verification, guardrails, prompt contents,
wire-to-domain mapping - never the creative content of a real model
(ADR-008).
"""

import json
from pathlib import Path

import pytest

from qaops.config import QAOpsSettings
from qaops.core.errors import InputTooLargeError, StageError
from qaops.llm import LLMResponseFormatError, MockLLMClient, PromptLoader
from qaops.models import GapSeverity, RequirementAnalysisResult, RequirementInput
from qaops.pipelines.test_design import (
    BusinessRuleExtractor,
    GapAnalyzer,
    RequirementAnalyzer,
    build_analysis_pipeline,
)

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

ANALYZER_RESPONSE = json.dumps(
    {
        "requirements": [
            {
                "title": "Login with valid credentials",
                "description": "A registered user logs in with a correct email and password.",
                "source_excerpt": "entering a correct email and password combination",
                "actors": ["Registered user"],
                "validations": ["email/password combination is correct"],
            },
            {
                "title": "Account lockout",
                "description": "The account locks after 5 consecutive failed attempts.",
                "source_excerpt": "After 5 consecutive failed attempts",
            },
        ]
    }
)

RULES_RESPONSE = json.dumps(
    {
        "rules": [
            {
                "requirement_id": "REQ-002",
                "rule": "The account locks after 5 consecutive failed login attempts.",
                "source_excerpt": "After 5 consecutive failed attempts",
            }
        ]
    }
)

GAPS_RESPONSE = json.dumps(
    {
        "gaps": [
            {
                "description": "Lockout duration is not specified.",
                "severity": "blocker",
                "requirement_id": "REQ-002",
                "suggested_question": "How long does the account lock last?",
            },
            {
                "description": "No measurable criterion for 'Login must be fast'.",
                "severity": "minor",
                "requirement_id": None,
                "suggested_question": "What is the target login response time?",
            },
        ]
    }
)


@pytest.fixture
def settings(tmp_path: Path) -> QAOpsSettings:
    return QAOpsSettings(output_dir=tmp_path / "out")


@pytest.fixture
def prompts() -> PromptLoader:
    return PromptLoader()  # real packaged v1 templates


def login_input() -> RequirementInput:
    return RequirementInput(
        text=(EXAMPLES_DIR / "login.md").read_text(encoding="utf-8"), source_name="login.md"
    )


def analyzed(settings: QAOpsSettings, prompts: PromptLoader) -> RequirementAnalysisResult:
    stage = RequirementAnalyzer(MockLLMClient([ANALYZER_RESPONSE]), prompts, settings)
    return stage.run(login_input())


class TestGoldenExamples:
    @pytest.mark.parametrize(
        "name", ["login.md", "checkout.md", "video_playback.md", "fund_transfer.md"]
    )
    def test_fixture_exists_and_fits_input_guardrail(self, name: str) -> None:
        text = (EXAMPLES_DIR / name).read_text(encoding="utf-8")
        assert len(text) > 200
        assert len(text) < QAOpsSettings().max_input_chars


class TestRequirementAnalyzer:
    def test_assigns_sequential_ids_and_maps_fields(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        result = analyzed(settings, prompts)
        assert [r.id for r in result.requirements] == ["REQ-001", "REQ-002"]
        assert result.requirements[0].actors == ["Registered user"]
        assert result.source_name == "login.md"
        assert "Forgot password" in result.source_text  # source retained downstream

    def test_prompt_contains_document_and_system_prompt_set(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        mock = MockLLMClient([ANALYZER_RESPONSE])
        RequirementAnalyzer(mock, prompts, settings).run(login_input())
        request = mock.requests[0]
        assert "Invalid email or password" in request.messages[0].content
        assert "senior QA engineer" in request.system
        assert request.temperature == settings.temperature

    def test_zero_requirements_is_a_stage_error(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        mock = MockLLMClient(['{"requirements": []}'])
        with pytest.raises(StageError, match="zero requirements"):
            RequirementAnalyzer(mock, prompts, settings).run(login_input())

    def test_oversized_input_fails_fast_without_llm_call(
        self, prompts: PromptLoader, tmp_path: Path
    ) -> None:
        small = QAOpsSettings(output_dir=tmp_path, max_input_chars=1000)
        mock = MockLLMClient([ANALYZER_RESPONSE])
        big = RequirementInput(text="x" * 1001, source_name="big.md")
        with pytest.raises(InputTooLargeError, match="QAOPS_MAX_INPUT_CHARS"):
            RequirementAnalyzer(mock, prompts, small).run(big)
        assert mock.call_count == 0

    def test_invalid_output_repairs_via_retry(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        mock = MockLLMClient(["not json", ANALYZER_RESPONSE])
        result = RequirementAnalyzer(mock, prompts, settings).run(login_input())
        assert len(result.requirements) == 2
        assert mock.call_count == 2

    def test_exhausted_retries_persist_failures(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        mock = MockLLMClient(["bad", "bad", "bad"])
        with pytest.raises(LLMResponseFormatError):
            RequirementAnalyzer(mock, prompts, settings).run(login_input())
        failures = list((settings.output_dir / "llm_failures").iterdir())
        assert len(failures) == 3


class TestBusinessRuleExtractor:
    def test_assigns_br_ids_and_links_requirements(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        base = analyzed(settings, prompts)
        mock = MockLLMClient([RULES_RESPONSE])
        result = BusinessRuleExtractor(mock, prompts, settings).run(base)
        assert [r.id for r in result.business_rules] == ["BR-001"]
        assert result.business_rules[0].requirement_id == "REQ-002"
        assert result.requirements == base.requirements  # untouched

    def test_prompt_supplies_assigned_ids_and_source(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        base = analyzed(settings, prompts)
        mock = MockLLMClient([RULES_RESPONSE])
        BusinessRuleExtractor(mock, prompts, settings).run(base)
        content = mock.requests[0].messages[0].content
        assert "REQ-001" in content and "REQ-002" in content
        assert "Forgot password" in content  # original document included

    def test_unknown_requirement_reference_is_a_stage_error(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        base = analyzed(settings, prompts)
        bad = json.dumps(
            {"rules": [{"requirement_id": "REQ-099", "rule": "invented", "source_excerpt": ""}]}
        )
        with pytest.raises(StageError, match="REQ-099"):
            BusinessRuleExtractor(MockLLMClient([bad]), prompts, settings).run(base)

    def test_empty_rules_is_valid(self, settings: QAOpsSettings, prompts: PromptLoader) -> None:
        base = analyzed(settings, prompts)
        mock = MockLLMClient(['{"rules": []}'])
        result = BusinessRuleExtractor(mock, prompts, settings).run(base)
        assert result.business_rules == []

    def test_requires_prior_analysis(self, settings: QAOpsSettings, prompts: PromptLoader) -> None:
        empty = RequirementAnalysisResult(source_name="x", source_text="y")
        with pytest.raises(StageError, match="RequirementAnalyzer first"):
            BusinessRuleExtractor(MockLLMClient([]), prompts, settings).run(empty)


class TestGapAnalyzer:
    def test_builds_gap_report_with_severities(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        base = analyzed(settings, prompts)
        result = GapAnalyzer(MockLLMClient([GAPS_RESPONSE]), prompts, settings).run(base)
        assert len(result.gap_report.gaps) == 2
        assert result.gap_report.has_blockers
        assert result.gap_report.gaps[0].severity is GapSeverity.BLOCKER
        assert result.gap_report.gaps[1].requirement_id is None  # document-wide gap

    def test_empty_gap_list_is_valid(self, settings: QAOpsSettings, prompts: PromptLoader) -> None:
        base = analyzed(settings, prompts)
        result = GapAnalyzer(MockLLMClient(['{"gaps": []}']), prompts, settings).run(base)
        assert result.gap_report.gaps == []
        assert not result.gap_report.has_blockers

    def test_unknown_requirement_reference_is_a_stage_error(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        base = analyzed(settings, prompts)
        bad = json.dumps(
            {
                "gaps": [
                    {
                        "description": "x",
                        "severity": "major",
                        "requirement_id": "REQ-777",
                        "suggested_question": "?",
                    }
                ]
            }
        )
        with pytest.raises(StageError, match="REQ-777"):
            GapAnalyzer(MockLLMClient([bad]), prompts, settings).run(base)

    def test_preserves_business_rules_from_previous_stage(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        base = analyzed(settings, prompts)
        with_rules = BusinessRuleExtractor(MockLLMClient([RULES_RESPONSE]), prompts, settings).run(
            base
        )
        result = GapAnalyzer(MockLLMClient([GAPS_RESPONSE]), prompts, settings).run(with_rules)
        assert [r.id for r in result.business_rules] == ["BR-001"]


class TestComposedPipeline:
    def test_full_analysis_run(self, settings: QAOpsSettings, prompts: PromptLoader) -> None:
        mock = MockLLMClient([ANALYZER_RESPONSE, RULES_RESPONSE, GAPS_RESPONSE])
        pipeline = build_analysis_pipeline(mock, prompts, settings)
        assert pipeline.stage_names == [
            "requirement_analyzer",
            "business_rule_extractor",
            "gap_analyzer",
        ]
        result = pipeline.run(login_input())
        assert isinstance(result, RequirementAnalysisResult)
        assert [r.id for r in result.requirements] == ["REQ-001", "REQ-002"]
        assert [r.id for r in result.business_rules] == ["BR-001"]
        assert result.gap_report.has_blockers
        assert mock.call_count == 3

    def test_no_scenarios_or_test_cases_exist_in_result(
        self, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        # Phase 2 boundary: the analysis result has no scenario/test-case fields.
        fields = set(RequirementAnalysisResult.model_fields)
        assert "scenarios" not in fields
        assert "test_cases" not in fields


class TestEvaluationMode:
    """TEMPORARY evaluation feature (ADR-019).

    Reduces generation at the source so a large PRD fits in one model
    response, with a deterministic code-side cap as a safety check.
    Superseded by document chunking in a future release.
    """

    def _extraction(self, count: int) -> str:
        return json.dumps(
            {"requirements": [{"title": f"R{i}", "description": "d"} for i in range(count)]}
        )

    def test_disabled_by_default(self) -> None:
        assert QAOpsSettings().evaluation_mode is False

    def test_default_prompt_has_no_evaluation_instruction(self) -> None:
        settings = QAOpsSettings(evaluation_mode=False)
        client = MockLLMClient([self._extraction(3)])
        RequirementAnalyzer(client, PromptLoader(), settings).run(
            RequirementInput(text="doc", source_name="x")
        )
        sent = client.requests[0].messages[0].content
        assert "IMPORTANT" not in sent
        assert "at most" not in sent
        assert "\n\n\n" not in sent  # no stray blank line from the placeholder

    def test_evaluation_mode_injects_limit_into_prompt(self) -> None:
        settings = QAOpsSettings(evaluation_mode=True, max_requirements=7)
        client = MockLLMClient([self._extraction(3)])
        RequirementAnalyzer(client, PromptLoader(), settings).run(
            RequirementInput(text="doc", source_name="x")
        )
        sent = client.requests[0].messages[0].content
        assert "at most 7 requirements" in sent

    def test_code_cap_enforced_when_model_ignores_instruction(self) -> None:
        settings = QAOpsSettings(evaluation_mode=True, max_requirements=10)
        client = MockLLMClient([self._extraction(25)])
        result = RequirementAnalyzer(client, PromptLoader(), settings).run(
            RequirementInput(text="doc", source_name="x")
        )
        assert len(result.requirements) == 10

    def test_no_cap_when_disabled(self) -> None:
        settings = QAOpsSettings(evaluation_mode=False, max_requirements=10)
        client = MockLLMClient([self._extraction(25)])
        result = RequirementAnalyzer(client, PromptLoader(), settings).run(
            RequirementInput(text="doc", source_name="x")
        )
        assert len(result.requirements) == 25

    def test_fewer_than_cap_is_unaffected(self) -> None:
        settings = QAOpsSettings(evaluation_mode=True, max_requirements=10)
        client = MockLLMClient([self._extraction(4)])
        result = RequirementAnalyzer(client, PromptLoader(), settings).run(
            RequirementInput(text="doc", source_name="x")
        )
        assert len(result.requirements) == 4

    def test_ids_remain_sequential_after_capping(self) -> None:
        settings = QAOpsSettings(evaluation_mode=True, max_requirements=3)
        client = MockLLMClient([self._extraction(25)])
        result = RequirementAnalyzer(client, PromptLoader(), settings).run(
            RequirementInput(text="doc", source_name="x")
        )
        assert [r.id for r in result.requirements] == ["REQ-001", "REQ-002", "REQ-003"]
