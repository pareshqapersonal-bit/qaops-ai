"""BusinessRuleExtractor: enriches the analysis result with business rules.

The prompt supplies the already-assigned REQ-* IDs; the model links each
rule to one of them. Stage code assigns BR-* IDs deterministically and
verifies every reference against the known requirement set - an unknown
reference is a loud StageError, never silently dropped (ADR-001).
"""

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.core.ids import business_rule_ids
from qaops.llm import LLMClient, PromptLoader
from qaops.models import BusinessRule, RequirementAnalysisResult
from qaops.pipelines.test_design._support import requirements_as_prompt_json, run_structured_stage
from qaops.pipelines.test_design.schemas import RuleExtraction

PROMPT_NAME = "rule_extractor"


class BusinessRuleExtractor:
    """Extracts discrete business rules linked to known requirements."""

    name = "business_rule_extractor"

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
            schema=RuleExtraction,
            source_text=data.source_text,
            requirements_json=requirements_as_prompt_json(list(data.requirements)),
        )

        known_ids = {r.id for r in data.requirements}
        unknown = sorted({w.requirement_id for w in extraction.rules} - known_ids)
        if unknown:
            raise StageError(
                self.name,
                f"Model referenced unknown requirement IDs: {unknown}. "
                f"Known IDs: {sorted(known_ids)}.",
            )

        ids = business_rule_ids()
        rules = [
            BusinessRule(
                id=ids.next(),
                requirement_id=wire.requirement_id,
                rule=wire.rule,
                source_excerpt=wire.source_excerpt,
            )
            for wire in extraction.rules
        ]
        return RequirementAnalysisResult(
            source_name=data.source_name,
            source_text=data.source_text,
            requirements=data.requirements,
            business_rules=rules,
            gap_report=data.gap_report,
        )
