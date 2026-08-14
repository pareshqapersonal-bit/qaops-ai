"""Phase 40A tests: gap_analyzer tolerates null-sentinel requirement IDs.

Provider-agnostic robustness fix (ADR-056): a model may serialize a nullable
requirement_id as the string "null"/"none"/"" instead of a real JSON null (observed
from Nemotron in production). These unambiguous sentinels are normalized to None
before ID validation, so a requirement-agnostic gap is not misread as referencing an
unknown ID. Any other string (including "REQ-999"/"abc") is left intact and still
fails validation. This does not touch provider selection.
"""

import json
from pathlib import Path

import pytest

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.llm import MockLLMClient, PromptLoader
from qaops.models import RequirementAnalysisResult, RequirementInput
from qaops.pipelines.test_design import RequirementAnalyzer
from qaops.pipelines.test_design.gaps import GapAnalyzer, _normalize_requirement_id

_ANALYZER_RESPONSE = json.dumps(
    {
        "requirements": [
            {
                "title": "Login with valid credentials",
                "description": "A registered user logs in with a correct email and password.",
                "source_excerpt": "entering a correct email and password combination",
            },
            {
                "title": "Account lockout",
                "description": "The account locks after 5 consecutive failed attempts.",
                "source_excerpt": "After 5 consecutive failed attempts",
            },
        ]
    }
)


@pytest.fixture
def settings(tmp_path: Path) -> QAOpsSettings:
    return QAOpsSettings(output_dir=tmp_path / "out")


@pytest.fixture
def prompts() -> PromptLoader:
    return PromptLoader()


@pytest.fixture
def base(settings: QAOpsSettings, prompts: PromptLoader) -> RequirementAnalysisResult:
    stage = RequirementAnalyzer(MockLLMClient([_ANALYZER_RESPONSE]), prompts, settings)
    return stage.run(RequirementInput(text="A user logs in. After 5 failures the account locks."))


def _gap(requirement_id: object) -> str:
    return json.dumps(
        {
            "gaps": [
                {
                    "description": "A gap not tied to any specific requirement.",
                    "severity": "minor",
                    "requirement_id": requirement_id,
                    "suggested_question": "Which requirement does this relate to?",
                }
            ]
        }
    )


# -- The normalization helper in isolation ------------------------------------


class TestNormalizeHelper:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, None),
            ("null", None),
            (" NULL ", None),
            ("None", None),
            (" none ", None),
            ("", None),
            ("   ", None),
            ("REQ-001", "REQ-001"),
            ("REQ-999", "REQ-999"),
            ("abc", "abc"),
        ],
    )
    def test_normalize(self, value: str | None, expected: str | None) -> None:
        assert _normalize_requirement_id(value) == expected


# -- Through the real GapAnalyzer stage ---------------------------------------


class TestGapAnalyzerNullSentinels:
    def test_real_json_null_accepted(
        self, base: RequirementAnalysisResult, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        result = GapAnalyzer(MockLLMClient([_gap(None)]), prompts, settings).run(base)
        assert len(result.gap_report.gaps) == 1
        assert result.gap_report.gaps[0].requirement_id is None

    def test_string_null_normalized(
        self, base: RequirementAnalysisResult, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        # The exact production failure case: model emitted the string "null".
        result = GapAnalyzer(MockLLMClient([_gap("null")]), prompts, settings).run(base)
        assert result.gap_report.gaps[0].requirement_id is None

    def test_uppercase_whitespace_null_normalized(
        self, base: RequirementAnalysisResult, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        result = GapAnalyzer(MockLLMClient([_gap(" NULL ")]), prompts, settings).run(base)
        assert result.gap_report.gaps[0].requirement_id is None

    def test_none_string_normalized(
        self, base: RequirementAnalysisResult, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        result = GapAnalyzer(MockLLMClient([_gap("none")]), prompts, settings).run(base)
        assert result.gap_report.gaps[0].requirement_id is None

    def test_empty_string_normalized(
        self, base: RequirementAnalysisResult, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        result = GapAnalyzer(MockLLMClient([_gap("")]), prompts, settings).run(base)
        assert result.gap_report.gaps[0].requirement_id is None

    def test_whitespace_only_normalized(
        self, base: RequirementAnalysisResult, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        result = GapAnalyzer(MockLLMClient([_gap("   ")]), prompts, settings).run(base)
        assert result.gap_report.gaps[0].requirement_id is None

    def test_valid_req_id_unchanged(
        self, base: RequirementAnalysisResult, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        result = GapAnalyzer(MockLLMClient([_gap("REQ-001")]), prompts, settings).run(base)
        assert result.gap_report.gaps[0].requirement_id == "REQ-001"

    def test_unknown_id_still_fails(
        self, base: RequirementAnalysisResult, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        with pytest.raises(StageError, match="unknown requirement IDs"):
            GapAnalyzer(MockLLMClient([_gap("REQ-999")]), prompts, settings).run(base)

    def test_arbitrary_string_still_fails(
        self, base: RequirementAnalysisResult, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        with pytest.raises(StageError, match="unknown requirement IDs"):
            GapAnalyzer(MockLLMClient([_gap("abc")]), prompts, settings).run(base)

    def test_production_failure_case_now_passes(
        self, base: RequirementAnalysisResult, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        # Reproduces the real run: known IDs REQ-001..REQ-002, a gap with "null".
        # Previously raised "Model referenced unknown requirement IDs: ['null']".
        result = GapAnalyzer(MockLLMClient([_gap("null")]), prompts, settings).run(base)
        assert result.gap_report.gaps  # stage completed, gap retained as null-ref
        assert result.gap_report.gaps[0].requirement_id is None

    def test_existing_behavior_valid_and_null_mixed(
        self, base: RequirementAnalysisResult, settings: QAOpsSettings, prompts: PromptLoader
    ) -> None:
        # A real valid ID plus a "null" sentinel: valid kept, sentinel -> None.
        payload = json.dumps(
            {
                "gaps": [
                    {"description": "Tied gap.", "severity": "major", "requirement_id": "REQ-002"},
                    {"description": "Untied gap.", "severity": "minor", "requirement_id": "null"},
                ]
            }
        )
        result = GapAnalyzer(MockLLMClient([payload]), prompts, settings).run(base)
        ids = [g.requirement_id for g in result.gap_report.gaps]
        assert ids == ["REQ-002", None]
