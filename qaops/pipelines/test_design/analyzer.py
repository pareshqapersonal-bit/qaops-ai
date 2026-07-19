"""RequirementAnalyzer: RequirementInput -> RequirementAnalysisResult.

Single responsibility: structured requirement extraction. Gap analysis
is deliberately a separate stage (GapAnalyzer) with its own prompt and
output, per the Phase 2 design decision to split extraction from
ambiguity analysis.

The stage enforces the input-size guardrail (ADR-006), delegates
generation to the LLM through generate_structured, then assigns REQ-*
IDs deterministically while mapping wire objects into strict domain
models (ADR-001, ADR-011).
"""

from qaops.config import QAOpsSettings
from qaops.core.errors import InputTooLargeError, StageError
from qaops.core.ids import requirement_ids
from qaops.llm import LLMClient, PromptLoader
from qaops.models import Requirement, RequirementAnalysisResult, RequirementInput
from qaops.pipelines.test_design._support import run_structured_stage
from qaops.pipelines.test_design.schemas import RequirementExtraction

PROMPT_NAME = "analyzer"


class RequirementAnalyzer:
    """Extracts structured requirements from raw requirement text."""

    name = "requirement_analyzer"

    def __init__(self, client: LLMClient, prompts: PromptLoader, settings: QAOpsSettings) -> None:
        self._client = client
        self._prompts = prompts
        self._settings = settings

    def run(self, data: RequirementInput) -> RequirementAnalysisResult:
        if len(data.text) > self._settings.max_input_chars:
            msg = (
                f"Input '{data.source_name}' is {len(data.text)} characters; "
                f"the configured limit is {self._settings.max_input_chars} "
                "(QAOPS_MAX_INPUT_CHARS). Split the document and analyze the "
                "parts separately."
            )
            raise InputTooLargeError(msg)

        extraction = run_structured_stage(
            client=self._client,
            prompts=self._prompts,
            settings=self._settings,
            prompt_name=PROMPT_NAME,
            schema=RequirementExtraction,
            requirement_text=data.text,
        )
        if not extraction.requirements:
            raise StageError(
                self.name,
                f"Model extracted zero requirements from '{data.source_name}'. "
                "The input may not contain requirements, or the prompt needs review.",
            )

        ids = requirement_ids()
        requirements = [
            Requirement(id=ids.next(), **wire.model_dump()) for wire in extraction.requirements
        ]
        return RequirementAnalysisResult(
            source_name=data.source_name,
            source_text=data.text,
            requirements=requirements,
        )
