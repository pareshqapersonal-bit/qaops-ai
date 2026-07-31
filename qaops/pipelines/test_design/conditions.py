"""TestConditionAnalyzer: ScenarioDesignResult -> ConditionDesignResult (ADR-036).

The Phase 21 stage between scenario generation and test-case generation. It
decomposes each scenario into materially distinct, evidence-bound TEST
CONDITIONS using relevant QA techniques (positive/negative, equivalence,
boundary, decision-table/business-rule, state-transition, validation,
eligibility, alternate-flow, data/role variation, combination), applying a
technique only when the supplied requirements/rules make it relevant.

Division of labour (ADR-036), mirroring the rest of the pipeline (ADR-001/015):
- the model returns ID-less wire conditions referencing supplied REQ-*/BR-*/SC-*
  IDs and a source_basis;
- stage code verifies every reference against the known sets, ENFORCES the
  evidence rule (a derived condition must cite the rule/requirement that carries
  its basis), assigns COND-* IDs deterministically, removes exact/semantic
  duplicates via a canonical signature, and applies expansion bounds.

Ambiguity handling: a condition whose expected behaviour is not established by
evidence arrives with status=unresolved and gap_reference text. It is preserved
(never dropped, never given an invented answer); Phase 21's gap linkage records
it, and coverage never counts it as covered.

Bounds: max_conditions_per_scenario caps per-scenario expansion; a hit sets
expansion_truncated so coverage is reported as non-exhaustive rather than
silently dropping candidates.
"""

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.core.ids import condition_ids
from qaops.llm import LLMClient, PromptLoader
from qaops.models import (
    ConditionDesignResult,
    Gap,
    ScenarioDesignResult,
    TestCondition,
)
from qaops.models.enums import ConditionStatus, GapSeverity, SourceBasis
from qaops.pipelines.test_design._support import requirements_as_prompt_json, run_structured_stage
from qaops.pipelines.test_design.schemas import ExtractedTestCondition, TestConditionExtraction

PROMPT_NAME = "test_condition_analyzer"


def _normalize_gap_text(text: str) -> str:
    """Normalize gap text for dedup: lowercase, collapse whitespace/punctuation."""
    return " ".join("".join(c if c.isalnum() else " " for c in text.casefold()).split())


# source_basis values that require a documented numeric/class/state/combination
# basis. A condition claiming one of these must cite at least one business rule
# or requirement that carries the basis, otherwise it is unsupported derivation.
_DERIVED_BASES = frozenset(
    {
        SourceBasis.DERIVED_BOUNDARY,
        SourceBasis.DERIVED_EQUIVALENCE,
        SourceBasis.DOCUMENTED_COMBINATION,
        SourceBasis.DOCUMENTED_STATE_TRANSITION,
    }
)


def _canonical_signature(wire: ExtractedTestCondition) -> tuple[str, ...]:
    """Deterministic semantic signature for dedup (ADR-036).

    Built from meaning-bearing fields, NOT the description text alone: scenario,
    category, source basis, sorted rule/requirement refs, and the normalized
    parameter set. Because parameters are part of the signature, boundary
    variants (quantity=1 vs 2 vs 3) have DIFFERENT signatures and are preserved,
    while a genuine restatement of the same proposition collapses.
    """
    params = ";".join(
        f"{k.strip().casefold()}={v.strip().casefold()}" for k, v in sorted(wire.parameters.items())
    )
    return (
        wire.scenario_id,
        wire.category.value,
        wire.source_basis.value,
        ",".join(sorted(wire.requirement_ids)),
        ",".join(sorted(wire.business_rule_ids)),
        params,
        " ".join(wire.description.casefold().split()) if not params else "",
    )


class TestConditionAnalyzer:
    """Derives evidence-bound test conditions from designed scenarios."""

    # Domain/pipeline class, not a pytest test class, despite the Test* name.
    __test__ = False

    name = "test_condition_analyzer"

    def __init__(self, client: LLMClient, prompts: PromptLoader, settings: QAOpsSettings) -> None:
        self._client = client
        self._prompts = prompts
        self._settings = settings

    def run(self, data: ScenarioDesignResult) -> ConditionDesignResult:
        if not data.scenarios:
            raise StageError(self.name, "No scenarios present; run ScenarioGenerator first.")

        analysis = data.analysis
        extraction = run_structured_stage(
            client=self._client,
            prompts=self._prompts,
            settings=self._settings,
            prompt_name=PROMPT_NAME,
            schema=TestConditionExtraction,
            scenarios_json=requirements_as_prompt_json(list(data.scenarios)),
            requirements_json=requirements_as_prompt_json(list(analysis.requirements)),
            rules_json=requirements_as_prompt_json(list(analysis.business_rules)),
        )
        if not extraction.conditions:
            raise StageError(
                self.name,
                f"Model generated zero test conditions for '{analysis.source_name}'. "
                "The prompt or the scenario output needs review.",
            )

        self._validate_references(data, analysis, extraction)
        self._validate_evidence(extraction)

        # Deterministic dedup by canonical signature, then per-scenario bounds.
        deduped = self._dedup(extraction.conditions)
        bounded, truncated, note = self._apply_bounds(deduped)

        ids = condition_ids()
        conditions = [
            TestCondition(
                id=ids.next(),
                scenario_id=wire.scenario_id,
                requirement_ids=wire.requirement_ids,
                business_rule_ids=wire.business_rule_ids,
                category=wire.category,
                description=wire.description,
                rationale=wire.rationale,
                source_basis=wire.source_basis,
                status=wire.status,
                parameters=wire.parameters,
                gap_reference=wire.gap_reference,
            )
            for wire in bounded
        ]

        # Ambiguity handling (ADR-036): every unresolved condition becomes a gap
        # so the missing behaviour is tracked, without inventing an answer and
        # without duplicating an equivalent gap already found by GapAnalyzer.
        enriched_design = self._merge_unresolved_gaps(data, conditions)
        return ConditionDesignResult(
            scenario_design=enriched_design,
            conditions=conditions,
            expansion_truncated=truncated,
            truncation_note=note,
        )

    def _merge_unresolved_gaps(
        self, data: ScenarioDesignResult, conditions: list[TestCondition]
    ) -> ScenarioDesignResult:
        analysis = data.analysis
        existing = list(analysis.gap_report.gaps)
        seen = {_normalize_gap_text(g.description) for g in existing}
        added: list[Gap] = []
        for cond in conditions:
            if cond.status is not ConditionStatus.UNRESOLVED:
                continue
            text = cond.gap_reference.strip() or (
                f"Expected behaviour for condition {cond.description!r} is not specified."
            )
            key = _normalize_gap_text(text)
            if key in seen:
                continue
            seen.add(key)
            req_id = cond.requirement_ids[0] if cond.requirement_ids else None
            added.append(
                Gap(
                    description=text,
                    severity=GapSeverity.MAJOR,
                    requirement_id=req_id,
                    suggested_question=text,
                )
            )
        if not added:
            return data
        new_report = analysis.gap_report.model_copy(update={"gaps": [*existing, *added]})
        new_analysis = analysis.model_copy(update={"gap_report": new_report})
        return data.model_copy(update={"analysis": new_analysis})

    # --- validation ----------------------------------------------------------

    def _validate_references(
        self,
        data: ScenarioDesignResult,
        analysis: object,
        extraction: TestConditionExtraction,
    ) -> None:
        known_scenarios = {s.id for s in data.scenarios}
        unknown_scenarios = sorted({w.scenario_id for w in extraction.conditions} - known_scenarios)
        if unknown_scenarios:
            raise StageError(
                self.name,
                f"Model referenced unknown scenario IDs: {unknown_scenarios}. "
                f"Known IDs: {sorted(known_scenarios)}.",
            )

        known_requirements = {r.id for r in analysis.requirements}  # type: ignore[attr-defined]
        unknown_requirements = sorted(
            {rid for w in extraction.conditions for rid in w.requirement_ids} - known_requirements
        )
        if unknown_requirements:
            raise StageError(
                self.name,
                f"Model referenced unknown requirement IDs: {unknown_requirements}. "
                f"Known IDs: {sorted(known_requirements)}.",
            )

        known_rules = {r.id for r in analysis.business_rules}  # type: ignore[attr-defined]
        unknown_rules = sorted(
            {rid for w in extraction.conditions for rid in w.business_rule_ids} - known_rules
        )
        if unknown_rules:
            raise StageError(
                self.name,
                f"Model referenced unknown business rule IDs: {unknown_rules}. "
                f"Known IDs: {sorted(known_rules)}.",
            )

        # A condition's requirements must belong to ITS scenario (ADR-014), the
        # same cross-link integrity the test-case stage enforces.
        scenario_reqs = {s.id: set(s.requirement_ids) for s in data.scenarios}
        for wire in extraction.conditions:
            allowed = scenario_reqs[wire.scenario_id]
            stray = sorted(set(wire.requirement_ids) - allowed)
            if stray:
                raise StageError(
                    self.name,
                    f"Condition {wire.description!r} under scenario {wire.scenario_id} "
                    f"references requirement IDs {stray} not linked to that scenario "
                    f"(allowed: {sorted(allowed)}).",
                )

    def _validate_evidence(self, extraction: TestConditionExtraction) -> None:
        """Enforce the evidence rule (ADR-036).

        Every condition must cite at least one evidence reference (requirement,
        rule, or - for a pure scenario-basis condition - its scenario). A
        DERIVED basis additionally requires a rule or requirement reference that
        carries the documented limit/class/state the derivation rests on, so a
        boundary/equivalence condition cannot be conjured without the rule that
        justifies it.
        """
        for wire in extraction.conditions:
            has_ref = bool(wire.requirement_ids or wire.business_rule_ids)
            if wire.source_basis in _DERIVED_BASES and not has_ref:
                raise StageError(
                    self.name,
                    f"Condition {wire.description!r} claims derived basis "
                    f"{wire.source_basis.value!r} but cites no requirement or "
                    "business rule as evidence. Derived conditions must reference "
                    "the documented rule/limit they are derived from.",
                )
            # Scenario-only basis is allowed (the scenario itself is evidence),
            # but an explicit-requirement/-rule basis must actually cite one.
            if wire.source_basis is SourceBasis.EXPLICIT_REQUIREMENT and not wire.requirement_ids:
                raise StageError(
                    self.name,
                    f"Condition {wire.description!r} claims basis explicit_requirement "
                    "but references no requirement ID.",
                )
            if wire.source_basis is SourceBasis.EXPLICIT_RULE and not wire.business_rule_ids:
                raise StageError(
                    self.name,
                    f"Condition {wire.description!r} claims basis explicit_rule "
                    "but references no business rule ID.",
                )
            # An unresolved condition must say what is ambiguous, so the gap is
            # traceable; a resolved one must not smuggle in an empty answer.
            if wire.status is ConditionStatus.UNRESOLVED and not wire.gap_reference.strip():
                raise StageError(
                    self.name,
                    f"Condition {wire.description!r} is marked unresolved but gives "
                    "no gap_reference describing the missing behaviour.",
                )

    # --- dedup & bounds ------------------------------------------------------

    def _dedup(self, wires: list[ExtractedTestCondition]) -> list[ExtractedTestCondition]:
        seen: set[tuple[str, ...]] = set()
        result: list[ExtractedTestCondition] = []
        for wire in wires:
            sig = _canonical_signature(wire)
            if sig in seen:
                continue
            seen.add(sig)
            result.append(wire)
        return result

    def _apply_bounds(
        self, wires: list[ExtractedTestCondition]
    ) -> tuple[list[ExtractedTestCondition], bool, str]:
        cap = self._settings.max_conditions_per_scenario
        per_scenario: dict[str, int] = {}
        kept: list[ExtractedTestCondition] = []
        dropped_scenarios: set[str] = set()
        for wire in wires:
            count = per_scenario.get(wire.scenario_id, 0)
            if cap and count >= cap:
                dropped_scenarios.add(wire.scenario_id)
                continue
            per_scenario[wire.scenario_id] = count + 1
            kept.append(wire)
        if dropped_scenarios:
            note = (
                f"Expansion limit reached ({cap} conditions/scenario); additional "
                f"candidate conditions remain for scenarios {sorted(dropped_scenarios)}."
            )
            return kept, True, note
        return kept, False, ""
