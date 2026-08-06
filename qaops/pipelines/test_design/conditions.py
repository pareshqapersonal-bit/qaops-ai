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

import logging

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
from qaops.models.enums import ConditionCategory, ConditionStatus, GapSeverity, SourceBasis
from qaops.pipelines.test_design._support import requirements_as_prompt_json, run_structured_stage
from qaops.pipelines.test_design.schemas import ExtractedTestCondition, TestConditionExtraction

PROMPT_NAME = "test_condition_analyzer"

logger = logging.getLogger(__name__)


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

# Phase 29: minimum length for a gap_reference to count as a substantive
# justification for an unresolved condition, and the bare confirm-with-someone
# phrases that name WHO to ask but not WHAT is missing (so they are not, on their
# own, a justification). Detection is deterministic and report-only.
_MIN_JUSTIFICATION_CHARS = 12
_BARE_CONFIRM_PHRASES = frozenset(
    {
        "confirm with the po",
        "confirm with po",
        "confirm with the product owner",
        "confirm with the client",
        "confirm with client",
        "confirm with the ba",
        "confirm with ba",
        "confirm with the business analyst",
        "tbd",
        "unknown",
        "n/a",
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
            gaps_json=requirements_as_prompt_json(list(analysis.gap_report.gaps)),
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

        # Phase 22 (ADR-037): deterministic gap -> unresolved integration. A gap
        # the requirement analysis already found can make a condition's expected
        # behaviour unknowable; such conditions must be UNRESOLVED and linked to
        # the gap, so a known ambiguity cannot coexist with 100% condition
        # coverage. Runs BEFORE gap synthesis so we do not duplicate gaps.
        conditions = self._apply_gap_linkage(conditions, analysis.gap_report.gaps)

        # Phase 29: deterministic justification check. Every unresolved condition
        # must be backed by a real gap and a substantive gap_reference. This
        # DETECTS AND REPORTS unjustified unresolved classifications; it never
        # reclassifies - the model remains responsible for the classification
        # (evidence-first is enforced by the prompt). Reporting only, so the
        # pipeline stays the sole author and behaviour is unchanged for callers.
        self._report_unjustified_unresolved(conditions, analysis.gap_report.gaps)

        # Ambiguity handling (ADR-036): every unresolved condition becomes a gap
        # so the missing behaviour is tracked, without inventing an answer and
        # without duplicating an equivalent gap already found by GapAnalyzer.
        enriched_design = self._merge_unresolved_gaps(data, conditions)
        self._log_expansion(data, conditions, truncated)
        return ConditionDesignResult(
            scenario_design=enriched_design,
            conditions=conditions,
            expansion_truncated=truncated,
            truncation_note=note,
        )

    def _apply_gap_linkage(
        self, conditions: list[TestCondition], gaps: list[Gap]
    ) -> list[TestCondition]:
        """Force a condition UNRESOLVED when a gap blocks its expected behaviour.

        Deterministic Step-4 integration (ADR-037). A gap affects a condition
        only when BOTH are true:
          * the gap is tied to a requirement the condition tests
            (gap.requirement_id in condition.requirement_ids), and
          * the gap concerns the SPECIFIC behaviour the condition checks, judged
            by keyword overlap between the gap text and the condition's
            description/parameters (so a gap about "exact tag copy" unresolves a
            copy-validation condition but not a tag-visibility condition).
        Informational gaps with no requirement link, or gaps whose subject does
        not match the condition, are left alone - we do NOT convert every gap
        into an unresolved condition. Conditions the model already marked
        unresolved are left as-is.
        """
        if not gaps:
            return conditions
        # Index gaps by the requirement they constrain.
        gaps_by_req: dict[str, list[Gap]] = {}
        for gap in gaps:
            if gap.requirement_id:
                gaps_by_req.setdefault(gap.requirement_id, []).append(gap)
        if not gaps_by_req:
            return conditions

        updated: list[TestCondition] = []
        for cond in conditions:
            if cond.status is ConditionStatus.UNRESOLVED:
                updated.append(cond)
                continue
            blocking = self._blocking_gap(cond, gaps_by_req)
            if blocking is None:
                updated.append(cond)
                continue
            # This gap makes the expected behaviour unknowable -> unresolve it,
            # preserving evidence and linking the gap. No fabricated answer.
            updated.append(
                cond.model_copy(
                    update={
                        "status": ConditionStatus.UNRESOLVED,
                        "gap_reference": blocking.description,
                    }
                )
            )
        return updated

    def _blocking_gap(self, cond: TestCondition, gaps_by_req: dict[str, list[Gap]]) -> Gap | None:
        """Return a gap that blocks this condition's expected behaviour, or None.

        Requires a requirement-link match AND subject-matter overlap. Subject
        overlap is decided by shared significant tokens between the gap text and
        the condition description/parameter values, which keeps the linkage
        specific: a gap about undefined tag COPY unresolves a copy-checking
        condition, not a mere visibility condition for the same requirement.
        """
        cond_tokens = self._significant_tokens(
            cond.description + " " + " ".join(cond.parameters.values())
        )
        for req_id in cond.requirement_ids:
            for gap in gaps_by_req.get(req_id, ()):  # only requirement-linked gaps
                gap_tokens = self._significant_tokens(
                    gap.description + " " + gap.suggested_question
                )
                overlap = cond_tokens & gap_tokens
                if len(overlap) >= 2:
                    return gap
        return None

    @staticmethod
    def _significant_tokens(text: str) -> set[str]:
        """Lowercase alphanumeric tokens >= 4 chars, minus common filler.

        Small deterministic helper for subject-matter overlap; not NLP, just
        enough to tell "tag copy/format/wording" apart from "tag visibility".
        """
        stop = {
            "when",
            "then",
            "with",
            "that",
            "this",
            "from",
            "into",
            "does",
            "will",
            "must",
            "item",
            "items",
            "cart",
            "user",
            "system",
            "shown",
            "show",
            "displayed",
            "display",
            "condition",
            "behaviour",
            "behavior",
            "eligible",
            "offer",
        }
        tokens = {
            t
            for t in "".join(c if c.isalnum() else " " for c in text.casefold()).split()
            if len(t) >= 4
        }
        return tokens - stop

    def _report_unjustified_unresolved(
        self, conditions: list[TestCondition], gaps: list[Gap]
    ) -> list[str]:
        """Detect and report unresolved conditions lacking a real justification.

        Phase 29 (detect-and-report only; never reclassifies). An unresolved
        condition is JUSTIFIED when it carries a substantive gap_reference - a
        specific statement of the missing information. It is UNJUSTIFIED when the
        gap_reference is missing, empty, or too trivial to name what is missing
        (e.g. a bare "confirm with the PO" with no stated open question). Such a
        classification is likely a false-positive unresolved that the evidence-
        first prompt should have resolved.

        Returns the list of offending condition ids (also logged as counts). The
        model remains responsible for classification; this only surfaces
        suspects for observability. It does not mutate conditions or gaps.
        """
        unjustified: list[str] = []
        for cond in conditions:
            if cond.status is not ConditionStatus.UNRESOLVED:
                continue
            reference = " ".join((cond.gap_reference or "").split())
            if not self._is_substantive_justification(reference):
                unjustified.append(cond.id)

        if unjustified:
            # Counts and ids only - never prompts, secrets, or document content.
            logger.warning(
                "test_condition_analyzer.unjustified_unresolved count=%d of unresolved=%d ids=%s",
                len(unjustified),
                sum(1 for c in conditions if c.status is ConditionStatus.UNRESOLVED),
                sorted(unjustified),
            )
        return unjustified

    @staticmethod
    def _is_substantive_justification(reference: str) -> bool:
        """True when a gap_reference genuinely names missing information.

        Deterministic and conservative: a justification is substantive when it is
        non-empty, long enough to state a specific open question, and not merely a
        bare confirm-with-someone placeholder carrying no actual open question.
        """
        if len(reference) < _MIN_JUSTIFICATION_CHARS:
            return False
        folded = reference.casefold()
        # A bare "confirm with the PO/client/BA" with nothing else stated is not a
        # justification - it names who to ask but not what is missing.
        return not any(
            folded == phrase or folded == phrase + "." for phrase in _BARE_CONFIRM_PHRASES
        )

    def _log_expansion(
        self, data: ScenarioDesignResult, conditions: list[TestCondition], truncated: bool
    ) -> None:
        """Deterministic, non-sensitive expansion diagnostics (ADR-037 Step 13).

        Logs only counts - never prompts, secrets, or document content - using
        the existing module logger.
        """
        scenario_count = len(data.scenarios)
        per_scenario: dict[str, int] = {}
        resolved = unresolved = derived = explicit = 0
        for c in conditions:
            per_scenario[c.scenario_id] = per_scenario.get(c.scenario_id, 0) + 1
            if c.status is ConditionStatus.UNRESOLVED:
                unresolved += 1
            else:
                resolved += 1
            if c.source_basis in _DERIVED_BASES:
                derived += 1
            else:
                explicit += 1
        distribution = sorted(per_scenario.values(), reverse=True)
        logger.info(
            "test_condition_analyzer.expansion scenarios=%d conditions=%d "
            "resolved=%d unresolved=%d derived=%d explicit=%d "
            "conditions_per_scenario=%s expansion_truncated=%s",
            scenario_count,
            len(conditions),
            resolved,
            unresolved,
            derived,
            explicit,
            distribution,
            truncated,
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
            self._check_category_consistency(wire)

    def _check_category_consistency(self, wire: ExtractedTestCondition) -> None:
        """Reject a NEGATIVE condition whose description asserts the criteria are
        met (ADR-037 Step 11 #21).

        This is the COND-006 class: a negative-category condition that reads
        "system detects X as not eligible WHEN the criteria are met" is
        internally contradictory. We only flag the clear, deterministic case: a
        negative condition whose text contains an explicit "criteria/conditions
        ... are met / when met" affirmation. We do not attempt fuzzy semantic
        judgement - just the unambiguous self-contradiction.
        """
        if wire.category is not ConditionCategory.NEGATIVE:
            return
        text = " ".join(wire.description.casefold().split())
        met_phrases = (
            "criteria are met",
            "conditions are met",
            "criteria met",
            "when met",
            "when the criteria are met",
            "when eligibility criteria are met",
            "requirements are met",
        )
        if any(p in text for p in met_phrases):
            raise StageError(
                self.name,
                f"Condition {wire.description!r} is categorised 'negative' but its "
                "description states the criteria ARE met, which is contradictory. "
                "A negative condition must describe criteria NOT being met.",
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
