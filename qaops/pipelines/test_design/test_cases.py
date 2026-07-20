"""TestCaseGenerator: ScenarioDesignResult -> TestDesignResult.

The final generation stage of the Test Design pipeline: turns each
scenario into one or more production-quality manual test cases with
preconditions, test data, ordered steps, expected results, priority,
type, tags, and full requirement/scenario traceability.

ADR-001/011/014 as usual:
- The model returns ID-less wire test cases referencing supplied SC-*
  and REQ-* IDs; stage code verifies every reference against the known
  sets and assigns TC-* IDs deterministically.
- Step numbers are assigned by code from list order - the model cannot
  produce mis-numbered steps; the domain model's 1..N validator remains
  as defense in depth.
- Exact duplicates (same scenario + normalized title) fail loudly
  (ADR-012); near-duplicate flagging is Phase 5.

Coverage is deliberately left at its default: computing it is Phase 5's
deterministic CoverageValidator, never this stage's job.
"""

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.core.ids import test_case_ids
from qaops.llm import LLMClient, PromptLoader
from qaops.models import ScenarioDesignResult, TestCase, TestDesignResult, TestStep
from qaops.pipelines.test_design._support import requirements_as_prompt_json, run_structured_stage
from qaops.pipelines.test_design.schemas import ExtractedTestCase, TestCaseExtraction

PROMPT_NAME = "test_case_generator"


def _dedup_key(wire: ExtractedTestCase) -> tuple[str, str]:
    normalized_title = " ".join(wire.title.casefold().split())
    return (wire.scenario_id, normalized_title)


class TestCaseGenerator:
    """Generates manual test cases from designed scenarios."""

    # Domain/pipeline class, not a pytest test class, despite the Test* name.
    __test__ = False

    name = "test_case_generator"

    def __init__(self, client: LLMClient, prompts: PromptLoader, settings: QAOpsSettings) -> None:
        self._client = client
        self._prompts = prompts
        self._settings = settings

    def run(self, data: ScenarioDesignResult) -> TestDesignResult:
        if not data.scenarios:
            raise StageError(self.name, "No scenarios present; run ScenarioGenerator first.")

        analysis = data.analysis
        extraction = run_structured_stage(
            client=self._client,
            prompts=self._prompts,
            settings=self._settings,
            prompt_name=PROMPT_NAME,
            schema=TestCaseExtraction,
            scenarios_json=requirements_as_prompt_json(list(data.scenarios)),
            requirements_json=requirements_as_prompt_json(list(analysis.requirements)),
            rules_json=requirements_as_prompt_json(list(analysis.business_rules)),
        )
        if not extraction.test_cases:
            raise StageError(
                self.name,
                f"Model generated zero test cases for '{analysis.source_name}'. "
                "The prompt or the scenario output needs review.",
            )

        known_scenarios = {s.id for s in data.scenarios}
        unknown_scenarios = sorted({w.scenario_id for w in extraction.test_cases} - known_scenarios)
        if unknown_scenarios:
            raise StageError(
                self.name,
                f"Model referenced unknown scenario IDs: {unknown_scenarios}. "
                f"Known IDs: {sorted(known_scenarios)}.",
            )

        known_requirements = {r.id for r in analysis.requirements}
        unknown_requirements = sorted(
            {rid for w in extraction.test_cases for rid in w.requirement_ids} - known_requirements
        )
        if unknown_requirements:
            raise StageError(
                self.name,
                f"Model referenced unknown requirement IDs: {unknown_requirements}. "
                f"Known IDs: {sorted(known_requirements)}.",
            )

        # Each case's requirements must belong to ITS scenario, not merely
        # exist somewhere in the run - this catches hallucinated cross-links
        # a global check would miss (ADR-014).
        scenario_reqs = {s.id: set(s.requirement_ids) for s in data.scenarios}
        for wire in extraction.test_cases:
            allowed = scenario_reqs[wire.scenario_id]
            stray = sorted(set(wire.requirement_ids) - allowed)
            if stray:
                raise StageError(
                    self.name,
                    f"Test case {wire.title!r} under scenario {wire.scenario_id} "
                    f"references requirement IDs {stray} not linked to that scenario "
                    f"(allowed: {sorted(allowed)}).",
                )

        seen: set[tuple[str, str]] = set()
        duplicates: list[str] = []
        for wire in extraction.test_cases:
            key = _dedup_key(wire)
            if key in seen:
                duplicates.append(f"{wire.title!r} ({wire.scenario_id})")
            seen.add(key)
        if duplicates:
            raise StageError(
                self.name,
                f"Model generated duplicate test cases despite instructions: {duplicates}.",
            )

        ids = test_case_ids()
        test_cases = [
            TestCase(
                id=ids.next(),
                scenario_id=wire.scenario_id,
                requirement_ids=wire.requirement_ids,
                module=wire.module,
                feature=wire.feature,
                title=wire.title,
                objective=wire.objective,
                preconditions=wire.preconditions,
                test_data=wire.test_data,
                # Step numbers come from list order, never the model (ADR-014).
                steps=[
                    TestStep(number=i, action=step.action, expected=step.expected)
                    for i, step in enumerate(wire.steps, start=1)
                ],
                expected_result=wire.expected_result,
                priority=wire.priority,
                test_type=wire.test_type,
                tags=wire.tags,
            )
            for wire in extraction.test_cases
        ]
        return TestDesignResult(
            source_name=analysis.source_name,
            requirements=analysis.requirements,
            business_rules=analysis.business_rules,
            gap_report=analysis.gap_report,
            scenarios=data.scenarios,
            test_cases=test_cases,
            # coverage stays default: Phase 5's deterministic validator owns it.
        )
