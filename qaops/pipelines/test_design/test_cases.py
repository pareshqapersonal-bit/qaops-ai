"""TestCaseGenerator: ConditionDesignResult -> TestDesignResult (ADR-036).

The final generation stage: turns each evidence-bound TEST CONDITION into one or
more production-quality manual test cases. Phase 21 changed the atomic unit from
scenario to condition, so expansion is driven by the conditions the analyzer
derived, not by a scenario:case ratio.

Division of labour (ADR-001/014/036):
- the model returns ID-less wire cases referencing supplied SC-*, COND-* and
  REQ-* IDs; stage code verifies every reference against the known sets and
  assigns TC-* IDs deterministically;
- step numbers come from list order, never the model;
- a case inherits its condition's provisional status: cases under an UNRESOLVED
  condition are marked provisional (their expected behaviour is not established
  by evidence) and are never presented as ordinary passing assertions;
- exact/near duplicates are removed by a canonical signature that includes the
  condition and expected result, so legitimate boundary variants survive;
- per-condition and total bounds cap expansion; a hit marks the result
  expansion_truncated rather than silently dropping cases.

Coverage is left at its default here: the deterministic CoverageValidator owns it.
"""

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.core.ids import test_case_ids
from qaops.llm import LLMClient, PromptLoader
from qaops.models import ConditionDesignResult, TestCase, TestCondition, TestDesignResult, TestStep
from qaops.models.enums import ConditionStatus
from qaops.pipelines.test_design._support import requirements_as_prompt_json, run_structured_stage
from qaops.pipelines.test_design.schemas import ExtractedTestCase, TestCaseExtraction

PROMPT_NAME = "test_case_generator"


def _canonical_signature(wire: ExtractedTestCase) -> tuple[str, ...]:
    """Deterministic semantic signature for test-case dedup (ADR-036).

    Uses meaning-bearing fields, not the title alone: condition, scenario,
    sorted requirements, normalized test-data values, and normalized expected
    result. Because test_data and expected_result are included, boundary
    variants (quantity 1 vs 2 vs 3) differ and are preserved, while a true
    restatement of the same case collapses.
    """
    data = ";".join(
        f"{k.strip().casefold()}={v.strip().casefold()}" for k, v in sorted(wire.test_data.items())
    )
    return (
        wire.condition_id,
        wire.scenario_id,
        ",".join(sorted(wire.requirement_ids)),
        data,
        " ".join(wire.expected_result.casefold().split()),
        " ".join(wire.title.casefold().split()) if not data else "",
    )


class TestCaseGenerator:
    """Generates manual test cases from derived test conditions."""

    # Domain/pipeline class, not a pytest test class, despite the Test* name.
    __test__ = False

    name = "test_case_generator"

    def __init__(self, client: LLMClient, prompts: PromptLoader, settings: QAOpsSettings) -> None:
        self._client = client
        self._prompts = prompts
        self._settings = settings

    def run(self, data: ConditionDesignResult) -> TestDesignResult:
        scenario_design = data.scenario_design
        analysis = scenario_design.analysis
        if not data.conditions:
            raise StageError(
                self.name, "No test conditions present; run TestConditionAnalyzer first."
            )

        extraction = run_structured_stage(
            client=self._client,
            prompts=self._prompts,
            settings=self._settings,
            prompt_name=PROMPT_NAME,
            schema=TestCaseExtraction,
            conditions_json=requirements_as_prompt_json(list(data.conditions)),
            scenarios_json=requirements_as_prompt_json(list(scenario_design.scenarios)),
            requirements_json=requirements_as_prompt_json(list(analysis.requirements)),
            rules_json=requirements_as_prompt_json(list(analysis.business_rules)),
        )
        if not extraction.test_cases:
            raise StageError(
                self.name,
                f"Model generated zero test cases for '{analysis.source_name}'. "
                "The prompt or the condition output needs review.",
            )

        conditions_by_id = {c.id: c for c in data.conditions}
        self._validate_references(scenario_design, analysis, conditions_by_id, extraction)

        # Deterministic dedup, then bounds. Signature dedup preserves boundary
        # variants (different test_data / expected_result) while collapsing
        # genuine restatements.
        deduped = self._dedup(extraction.test_cases)
        bounded, truncated_here, note = self._apply_bounds(deduped)

        ids = test_case_ids()
        test_cases = [
            TestCase(
                id=ids.next(),
                scenario_id=wire.scenario_id,
                condition_id=wire.condition_id,
                requirement_ids=wire.requirement_ids,
                # A case inherits its condition's provisional status.
                provisional=(
                    conditions_by_id[wire.condition_id].status is ConditionStatus.UNRESOLVED
                ),
                module=wire.module,
                feature=wire.feature,
                title=wire.title,
                objective=wire.objective,
                preconditions=wire.preconditions,
                test_data=wire.test_data,
                steps=[
                    TestStep(number=i, action=step.action, expected=step.expected)
                    for i, step in enumerate(wire.steps, start=1)
                ],
                expected_result=wire.expected_result,
                priority=wire.priority,
                test_type=wire.test_type,
                tags=wire.tags,
            )
            for wire in bounded
        ]

        truncated = data.expansion_truncated or truncated_here
        note = data.truncation_note or note
        return TestDesignResult(
            source_name=analysis.source_name,
            requirements=analysis.requirements,
            business_rules=analysis.business_rules,
            gap_report=analysis.gap_report,
            scenarios=scenario_design.scenarios,
            conditions=data.conditions,
            test_cases=test_cases,
            expansion_truncated=truncated,
            truncation_note=note,
            # coverage stays default: the deterministic validator owns it.
        )

    # --- validation ----------------------------------------------------------

    def _validate_references(
        self,
        scenario_design: object,
        analysis: object,
        conditions_by_id: dict[str, TestCondition],
        extraction: TestCaseExtraction,
    ) -> None:
        known_scenarios = {s.id for s in scenario_design.scenarios}  # type: ignore[attr-defined]
        unknown_scenarios = sorted({w.scenario_id for w in extraction.test_cases} - known_scenarios)
        if unknown_scenarios:
            raise StageError(
                self.name,
                f"Model referenced unknown scenario IDs: {unknown_scenarios}. "
                f"Known IDs: {sorted(known_scenarios)}.",
            )

        unknown_conditions = sorted(
            {w.condition_id for w in extraction.test_cases} - set(conditions_by_id)
        )
        if unknown_conditions:
            raise StageError(
                self.name,
                f"Model referenced unknown condition IDs: {unknown_conditions}. "
                f"Known IDs: {sorted(conditions_by_id)}.",
            )

        known_requirements = {r.id for r in analysis.requirements}  # type: ignore[attr-defined]
        unknown_requirements = sorted(
            {rid for w in extraction.test_cases for rid in w.requirement_ids} - known_requirements
        )
        if unknown_requirements:
            raise StageError(
                self.name,
                f"Model referenced unknown requirement IDs: {unknown_requirements}. "
                f"Known IDs: {sorted(known_requirements)}.",
            )

        # A case's scenario must match its condition's scenario, and its
        # requirements must belong to that condition (ADR-014/036): this catches
        # hallucinated cross-links a global check would miss.
        for wire in extraction.test_cases:
            condition = conditions_by_id[wire.condition_id]
            if wire.scenario_id != condition.scenario_id:
                raise StageError(
                    self.name,
                    f"Test case {wire.title!r} references scenario {wire.scenario_id} "
                    f"but its condition {wire.condition_id} belongs to scenario "
                    f"{condition.scenario_id}.",
                )
            allowed = set(condition.requirement_ids)
            stray = sorted(set(wire.requirement_ids) - allowed)
            if stray and allowed:
                raise StageError(
                    self.name,
                    f"Test case {wire.title!r} under condition {wire.condition_id} "
                    f"references requirement IDs {stray} not linked to that condition "
                    f"(allowed: {sorted(allowed)}).",
                )

    # --- dedup & bounds ------------------------------------------------------

    def _dedup(self, wires: list[ExtractedTestCase]) -> list[ExtractedTestCase]:
        seen: set[tuple[str, ...]] = set()
        result: list[ExtractedTestCase] = []
        duplicates: list[str] = []
        for wire in wires:
            sig = _canonical_signature(wire)
            if sig in seen:
                duplicates.append(f"{wire.title!r} ({wire.condition_id})")
                continue
            seen.add(sig)
            result.append(wire)
        # Duplicates are dropped deterministically, not raised: expansion is
        # expected to produce some near-duplicates the signature safely removes.
        return result

    def _apply_bounds(
        self, wires: list[ExtractedTestCase]
    ) -> tuple[list[ExtractedTestCase], bool, str]:
        per_condition_cap = self._settings.max_cases_per_condition
        total_cap = self._settings.max_total_test_cases
        per_condition: dict[str, int] = {}
        kept: list[ExtractedTestCase] = []
        truncated = False
        reasons: list[str] = []
        for wire in wires:
            if len(kept) >= total_cap:
                truncated = True
                reasons.append(f"total cap {total_cap} reached")
                break
            count = per_condition.get(wire.condition_id, 0)
            if per_condition_cap and count >= per_condition_cap:
                truncated = True
                continue
            per_condition[wire.condition_id] = count + 1
            kept.append(wire)
        note = ""
        if truncated:
            note = (
                f"Expansion limit reached (max {per_condition_cap} cases/condition, "
                f"{total_cap} total); additional candidate test cases remain."
            )
            if reasons:
                note = f"{note} ({'; '.join(sorted(set(reasons)))})"
        return kept, truncated, note
