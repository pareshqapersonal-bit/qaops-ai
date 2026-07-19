"""GapAnalyzer: enriches the analysis result with an Ambiguity & Gap Report.

Kept separate from RequirementAnalyzer by design: extraction and
ambiguity analysis are distinct outputs with distinct prompts, so each
is independently testable and replaceable. The gap report is a
first-class deliverable - it tells the user what a senior QA engineer
would ask the BA/PO before designing tests.

An empty gap list is a valid outcome (unambiguous input). Gap
references to requirement IDs are verified against the known set.
"""

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.llm import LLMClient, PromptLoader
from qaops.models import Gap, GapReport, RequirementAnalysisResult
from qaops.pipelines.test_design._support import requirements_as_prompt_json, run_structured_stage
from qaops.pipelines.test_design.schemas import GapExtraction

PROMPT_NAME = "gap_analyzer"


class GapAnalyzer:
    """Finds ambiguities, missing details, and undefined behaviors."""

    name = "gap_analyzer"

    def __init__(self, client: LLMClient, prompts: PromptLoader, settings: QAOpsSettings) -> None:
        self._client = client
        self._prompts = prompts
        self._settings = settings

    def run(self, data: RequirementAnalysisResult) -> RequirementAnalysisResult:
        if not data.requirements:
            raise StageError(self.name, "No requirements present; run RequirementAnalyzer first.")

        extraction = run_structured_stage(
            client=self._client,
            prompts=self._prompts,
            settings=self._settings,
            prompt_name=PROMPT_NAME,
            schema=GapExtraction,
            source_text=data.source_text,
            requirements_json=requirements_as_prompt_json(list(data.requirements)),
        )

        known_ids = {r.id for r in data.requirements}
        unknown = sorted(
            {w.requirement_id for w in extraction.gaps if w.requirement_id is not None} - known_ids
        )
        if unknown:
            raise StageError(
                self.name,
                f"Model referenced unknown requirement IDs: {unknown}. "
                f"Known IDs: {sorted(known_ids)}.",
            )

        gaps = [
            Gap(
                description=wire.description,
                severity=wire.severity,
                requirement_id=wire.requirement_id,
                suggested_question=wire.suggested_question,
            )
            for wire in extraction.gaps
        ]
        return RequirementAnalysisResult(
            source_name=data.source_name,
            source_text=data.source_text,
            requirements=data.requirements,
            business_rules=data.business_rules,
            gap_report=GapReport(gaps=gaps),
        )
