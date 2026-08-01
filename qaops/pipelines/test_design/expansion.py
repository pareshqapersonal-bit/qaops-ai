"""Deterministic test-case expansion planner (ADR-038, Phase 23).

Option C: a condition's already-derived technique (its ConditionCategory) plus
its documented parameters determine, deterministically, WHICH test-case variants
are required and HOW MANY. The LLM never decides the count - it only authors the
concrete steps/data/expected-result for each planned slot.

The planner is evidence-bound: it produces variant slots ONLY from what the
condition already documents. It never invents a numeric limit, a state, or a
rule. When a condition carries no expandable dimension, the planner emits a
single slot - a legitimate 1:1 outcome, not a failure.

Design invariants:
- Pure function of the TestCondition; no LLM, no I/O, fully deterministic.
- Slot count is bounded by settings.max_cases_per_condition (truncation flagged
  by the existing bounds logic downstream, not here).
- Every slot records why it exists (technique + variant_label + reason), so a
  reader can answer "why does this test case exist?" at the technique level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from qaops.models.enums import ConditionCategory, ConditionStatus

if TYPE_CHECKING:
    from qaops.models import TestCondition


@dataclass(frozen=True)
class ExpansionSlot:
    """One planned test-case variant for a condition (ADR-038).

    slot_id is unique within a condition's plan and is echoed by the LLM so its
    authored case can be mapped back to the slot deterministically.
    parameter_delta carries the concrete dimension value for this variant (e.g.
    {"quantity": "1"} for a below-boundary slot), taken only from the condition's
    documented parameters - never invented.
    """

    slot_id: str
    technique: str
    variant_label: str
    reason: str
    parameter_delta: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ConditionPlan:
    """The expansion plan for a single condition."""

    condition_id: str
    scenario_id: str
    slots: tuple[ExpansionSlot, ...]


@dataclass(frozen=True)
class ExpansionPlan:
    """The expansion plan for all conditions in a run."""

    per_condition: tuple[ConditionPlan, ...]

    @property
    def total_slots(self) -> int:
        return sum(len(cp.slots) for cp in self.per_condition)


# Categories whose expansion is intrinsically single-variant: the condition
# already states one specific proposition and there is nothing to fan out unless
# its parameters document multiple values (handled generically below).
_SINGLE_VARIANT = frozenset(
    {
        ConditionCategory.POSITIVE,
        ConditionCategory.NEGATIVE,
        ConditionCategory.VALIDATION,
        ConditionCategory.ELIGIBILITY,
        ConditionCategory.BUSINESS_RULE,
        ConditionCategory.ALTERNATE_FLOW,
        ConditionCategory.ERROR_HANDLING,
        ConditionCategory.ROLE_VARIATION,
        ConditionCategory.DATA_VARIATION,
        ConditionCategory.COMBINATION,
    }
)


def _numeric(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ExpansionPlanner:
    """Turns validated test conditions into deterministic expansion plans."""

    def __init__(self, max_slots_per_condition: int) -> None:
        # Upper bound on slots the planner will emit per condition. The real
        # per-condition/total case bounds are enforced later by the generator's
        # existing _apply_bounds; this simply keeps a single condition's plan
        # from exceeding the configured ceiling before authoring.
        self._max_slots = max_slots_per_condition

    def plan(self, conditions: list[TestCondition]) -> ExpansionPlan:
        return ExpansionPlan(per_condition=tuple(self._plan_condition(c) for c in conditions))

    def _plan_condition(self, cond: TestCondition) -> ConditionPlan:
        slots = self._slots_for(cond)
        # Deterministic ceiling; the generator's bounds own truncation flagging.
        slots = slots[: self._max_slots]
        return ConditionPlan(condition_id=cond.id, scenario_id=cond.scenario_id, slots=tuple(slots))

    def _slots_for(self, cond: TestCondition) -> list[ExpansionSlot]:
        # An unresolved condition (Phase 22) has no documented expected
        # behaviour, so there is nothing to expand: exactly one provisional
        # slot, never a fan-out that would imply we know the outcomes.
        if cond.status is ConditionStatus.UNRESOLVED:
            return [
                ExpansionSlot(
                    slot_id=f"{cond.id}-S1",
                    technique="provisional",
                    variant_label="unresolved",
                    reason="Expected behaviour is undocumented (linked to a gap); "
                    "a single provisional case records the intent.",
                )
            ]

        if cond.category is ConditionCategory.BOUNDARY:
            return self._boundary_slots(cond)
        if cond.category is ConditionCategory.EQUIVALENCE:
            return self._equivalence_slots(cond)
        if cond.category is ConditionCategory.STATE_TRANSITION:
            return self._state_slots(cond)
        # Single-variant techniques (and any unmapped category) -> one slot,
        # unless the condition's own parameters document multiple values.
        return self._single_or_parameterized(cond)

    # --- technique recipes ---------------------------------------------------

    def _boundary_slots(self, cond: TestCondition) -> list[ExpansionSlot]:
        """Below / at / above a documented numeric threshold.

        The threshold is taken ONLY from the condition's parameters. If no
        numeric parameter is present we cannot invent a limit, so we fall back to
        a single at-boundary slot rather than fabricating neighbours.
        """
        key, threshold = self._first_numeric_param(cond)
        if key is None or threshold is None:
            return [
                ExpansionSlot(
                    slot_id=f"{cond.id}-S1",
                    technique="boundary",
                    variant_label="at_boundary",
                    reason="Boundary condition without a numeric parameter; only "
                    "the documented point can be tested.",
                    parameter_delta=dict(cond.parameters),
                )
            ]
        below = self._format_delta(threshold - 1, threshold)
        above = self._format_delta(threshold + 1, threshold)
        at = self._format_delta(threshold, threshold)
        return [
            ExpansionSlot(
                slot_id=f"{cond.id}-S1",
                technique="boundary",
                variant_label="below_boundary",
                reason=f"One step below the documented threshold ({key}={below}).",
                parameter_delta={key: below},
            ),
            ExpansionSlot(
                slot_id=f"{cond.id}-S2",
                technique="boundary",
                variant_label="at_boundary",
                reason=f"At the documented threshold ({key}={at}).",
                parameter_delta={key: at},
            ),
            ExpansionSlot(
                slot_id=f"{cond.id}-S3",
                technique="boundary",
                variant_label="above_boundary",
                reason=f"One step above the documented threshold ({key}={above}).",
                parameter_delta={key: above},
            ),
        ]

    def _equivalence_slots(self, cond: TestCondition) -> list[ExpansionSlot]:
        """One representative per documented partition.

        Partitions come only from the condition parameters (e.g.
        {"class": "valid|invalid"} or an explicit eligibility value). With no
        documented partition we emit a single representative slot.
        """
        key, values = self._first_multivalue_param(cond)
        if key is None:
            return [
                ExpansionSlot(
                    slot_id=f"{cond.id}-S1",
                    technique="equivalence",
                    variant_label="representative",
                    reason="Equivalence condition with a single documented class.",
                    parameter_delta=dict(cond.parameters),
                )
            ]
        slots: list[ExpansionSlot] = []
        for i, val in enumerate(values, start=1):
            slots.append(
                ExpansionSlot(
                    slot_id=f"{cond.id}-S{i}",
                    technique="equivalence",
                    variant_label=f"partition_{val}",
                    reason=f"One representative of the documented '{val}' partition.",
                    parameter_delta={key: val},
                )
            )
        return slots

    def _state_slots(self, cond: TestCondition) -> list[ExpansionSlot]:
        """One slot per documented transition.

        Transitions are read from a parameter documenting states/transitions
        (e.g. {"transitions": "draft->submitted, submitted->approved"}). Without
        one, a single transition slot.
        """
        transitions = self._documented_transitions(cond)
        if not transitions:
            return [
                ExpansionSlot(
                    slot_id=f"{cond.id}-S1",
                    technique="state_transition",
                    variant_label="transition",
                    reason="State-transition condition with a single documented transition.",
                    parameter_delta=dict(cond.parameters),
                )
            ]
        slots: list[ExpansionSlot] = []
        for i, tr in enumerate(transitions, start=1):
            slots.append(
                ExpansionSlot(
                    slot_id=f"{cond.id}-S{i}",
                    technique="state_transition",
                    variant_label=tr,
                    reason=f"Documented transition '{tr}'.",
                    parameter_delta={"transition": tr},
                )
            )
        return slots

    def _single_or_parameterized(self, cond: TestCondition) -> list[ExpansionSlot]:
        """One slot for single-variant techniques, unless a parameter documents
        several behaviour-affecting values (data/role variation).
        """
        if cond.category in {ConditionCategory.DATA_VARIATION, ConditionCategory.ROLE_VARIATION}:
            key, values = self._first_multivalue_param(cond)
            if key is not None:
                return [
                    ExpansionSlot(
                        slot_id=f"{cond.id}-S{i}",
                        technique=cond.category.value,
                        variant_label=f"{key}_{val}",
                        reason=f"Documented {cond.category.value} value '{val}'.",
                        parameter_delta={key: val},
                    )
                    for i, val in enumerate(values, start=1)
                ]
        return [
            ExpansionSlot(
                slot_id=f"{cond.id}-S1",
                technique=cond.category.value,
                variant_label="representative",
                reason="Single documented behaviour; one case fully covers it.",
                parameter_delta=dict(cond.parameters),
            )
        ]

    # --- parameter helpers (documented-evidence only) ------------------------

    def _first_numeric_param(self, cond: TestCondition) -> tuple[str | None, float | None]:
        for key, raw in cond.parameters.items():
            num = _numeric(raw)
            if num is not None:
                return key, num
        return None, None

    def _first_multivalue_param(self, cond: TestCondition) -> tuple[str | None, list[str]]:
        """A parameter whose value documents several classes, split on | or comma.

        Only splits when the documented value itself lists multiple classes; a
        single scalar value is not fanned out (that would invent partitions).
        """
        for key, raw in cond.parameters.items():
            parts = [p.strip() for p in raw.replace("|", ",").split(",") if p.strip()]
            if len(parts) >= 2:
                return key, parts
        return None, []

    def _documented_transitions(self, cond: TestCondition) -> list[str]:
        for key, raw in cond.parameters.items():
            if "transition" in key.casefold() or "->" in raw:
                parts = [p.strip() for p in raw.split(",") if "->" in p]
                if parts:
                    return parts
        return []

    @staticmethod
    def _format_delta(value: float, threshold: float) -> str:
        # Integer thresholds stay integers (quantity 2 -> "1"/"3", not "1.0").
        if value == int(value) and threshold == int(threshold):
            return str(int(value))
        return str(value)
