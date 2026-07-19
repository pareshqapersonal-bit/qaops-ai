"""ScenarioGenerator: RequirementAnalysisResult -> ScenarioDesignResult.

Single responsibility: professional test scenario design across QA
techniques (functional, positive/negative, BVA, EP, input validation,
permissions, state transitions, integration, error handling). It does
NOT produce test cases, priorities, test data, or expected results -
those are Phase 4.

ADR-001/011 as usual: the model returns ID-less wire scenarios
referencing supplied REQ-* IDs; stage code verifies every reference,
assigns SC-* IDs deterministically, and maps into strict domain models.

Duplicate policy (ADR-012): exact duplicates within one generation
(same category + normalized title) are a loud StageError - the model
violated an explicit prompt instruction and the output cannot be
trusted as a scenario set. Near-duplicate *flagging* across the final
set remains Phase 5's heuristic Deduplicator.
"""

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.core.ids import scenario_ids
from qaops.llm import LLMClient, PromptLoader
from qaops.models import RequirementAnalysisResult, Scenario, ScenarioDesignResult
from qaops.pipelines.test_design._support import requirements_as_prompt_json, run_structured_stage
from qaops.pipelines.test_design.schemas import ExtractedScenario, ScenarioExtraction

PROMPT_NAME = "scenario_generator"


def _dedup_key(wire: ExtractedScenario) -> tuple[str, str]:
    normalized_title = " ".join(wire.title.casefold().split())
    return (wire.category.value, normalized_title)


class ScenarioGenerator:
    """Generates test scenarios grounded in analyzed requirements."""

    name = "scenario_generator"

    def __init__(self, client: LLMClient, prompts: PromptLoader, settings: QAOpsSettings) -> None:
        self._client = client
        self._prompts = prompts
        self._settings = settings

    def run(self, data: RequirementAnalysisResult) -> ScenarioDesignResult:
        if not data.requirements:
            raise StageError(self.name, "No requirements present; run RequirementAnalyzer first.")

        extraction = run_structured_stage(
            client=self._client,
            prompts=self._prompts,
            settings=self._settings,
            prompt_name=PROMPT_NAME,
            schema=ScenarioExtraction,
            requirements_json=requirements_as_prompt_json(list(data.requirements)),
            rules_json=requirements_as_prompt_json(list(data.business_rules)),
        )
        if not extraction.scenarios:
            raise StageError(
                self.name,
                f"Model generated zero scenarios for '{data.source_name}'. "
                "The prompt or the analysis output needs review.",
            )

        known_ids = {r.id for r in data.requirements}
        referenced = {rid for wire in extraction.scenarios for rid in wire.requirement_ids}
        unknown = sorted(referenced - known_ids)
        if unknown:
            raise StageError(
                self.name,
                f"Model referenced unknown requirement IDs: {unknown}. "
                f"Known IDs: {sorted(known_ids)}.",
            )

        seen: dict[tuple[str, str], str] = {}
        duplicates: list[str] = []
        for wire in extraction.scenarios:
            key = _dedup_key(wire)
            if key in seen:
                duplicates.append(f"{wire.title!r} ({wire.category.value})")
            else:
                seen[key] = wire.title
        if duplicates:
            raise StageError(
                self.name,
                f"Model generated duplicate scenarios despite instructions: {duplicates}.",
            )

        ids = scenario_ids()
        scenarios = [
            Scenario(
                id=ids.next(),
                title=wire.title,
                description=wire.description,
                category=wire.category,
                requirement_ids=wire.requirement_ids,
            )
            for wire in extraction.scenarios
        ]
        return ScenarioDesignResult(analysis=data, scenarios=scenarios)
