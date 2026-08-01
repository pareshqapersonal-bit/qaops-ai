# ADR-038: Technique-driven test-case expansion

**Status:** Accepted · **Date:** 2026-08-01 · **Relates to:** ADR-036 (test conditions), ADR-037 (condition expansion & gaps), ADR-011/014/015 (LLM generates, code validates)

## Context

Phases 21-22 fixed the scenario->condition layer: one scenario now yields many
evidence-bound conditions, and gaps deterministically produce unresolved
conditions. But production showed the bottleneck had moved one layer down:

    7 scenarios -> 11 conditions -> 11 cases

The condition->case step was effectively 1:1. Root cause (from the code): the
case prompt was restrictive-by-default ("more than one case ONLY when... do NOT
pad"), the generator was a single all-conditions call under token pressure, and
the rich condition structure (`category`, `parameters`) was passed but never
operationalised — the technique label was decorative at the case layer. A
`boundary` condition documenting "quantity >= 2" produced one case, not the
below/at/above trio the technique requires.

## Decision — Option C: a technique-directed single stage

Expansion is split into a deterministic planner and an LLM author, inside the
existing `TestCaseGenerator` (no new pipeline stage):

1. **`ExpansionPlanner` [deterministic].** For each condition it reads the
   already-derived `category` + `parameters` + `source_basis` and emits a small,
   bounded set of `ExpansionSlot`s — the specific variants the technique
   requires, drawn ONLY from documented evidence. The planner decides HOW MANY
   cases and WHICH variants.
2. **LLM author [LLM].** Given the plan, the model writes exactly one concrete,
   executable case per slot, echoing `slot_id`. It authors CONTENT; it does not
   decide the count.
3. **Deterministic post-pass [reused].** Existing Phase 21/22 machinery assigns
   `TC-*` IDs, dedups by canonical signature, applies per-condition/total bounds
   with `expansion_truncated`, inherits provisional status, and validates
   references — all unchanged.

This gives per-technique rigour and auditability with one stage and one prompt,
and makes the case count a deterministic function of documented dimensions, not
model whim.

## Technique recipes (evidence-bound)

| Technique (ConditionCategory) | Slots produced | Guard against over-generation |
|---|---|---|
| boundary | below / at / above the documented numeric param | no numeric param -> single at-boundary slot (never invents a number) |
| equivalence | one representative per documented partition | single class -> one slot |
| state_transition | one per documented transition | single transition -> one slot |
| data_variation / role_variation | one per documented value | single value -> one slot |
| combination | one (the condition already IS one documented decision-table row) | Cartesian guard lives in the analyzer, not here |
| positive / negative / validation / eligibility / business_rule / alternate_flow / error_handling | one representative slot | never fabricates extra paths |
| any category, status=unresolved | exactly one provisional slot | never fans out undocumented outcomes |

The planner only fans out from values the condition already documents. It never
invents a boundary, a partition, or a state. A condition with a single
documented dimension legitimately yields one case — 1:1 is correct there, not a
bug (consistent with ADR-037's "legitimate vs pathological 1:1").

## LLM vs deterministic responsibilities

- **Deterministic:** slot enumeration (count + variants), IDs, dedup, bounds,
  traceability, coverage, `expansion_truncated`.
- **LLM:** interpreting which documented value each slot needs; authoring
  concrete steps/data/expected-result; nothing about quantity.

## Traceability

Each generated `TestCase` carries optional `slot_id` and `technique`, so "why
does this case exist?" is answerable at the technique level (e.g. "COND-004
boundary, slot below_boundary, quantity=1"). Both fields are optional/defaulted:
pre-Phase-23 artifacts stay valid and API/export/frontend consumers are
unaffected. The REQ->BR->SC->COND->TC chain is otherwise unchanged.

## Deduplication

The existing canonical signature — `(condition_id, scenario, requirement_ids,
normalized test_data, normalized expected_result)` — already preserves variants
that differ by `test_data` (the boundary/partition values) while collapsing true
restatements. It is reused verbatim; two authored cases for the same slot with
the same data collapse to one.

## Coverage

Condition-coverage semantics are unchanged: a resolved condition is covered by
>=1 non-provisional case; unresolved is never covered. Expanding a condition to
N cases does not change its coverage contract. No coverage-model change; API,
exports, and frontend contracts preserved.

## Consequences

- A boundary condition now yields 3 cases; an N-partition equivalence condition
  yields N — deterministically, from documented values only.
- The count is auditable and bounded; the planner is code, not a prompt, so the
  expansion is reviewable and cannot drift with model behaviour.
- "Correct cases, not more cases": single-dimension conditions stay 1:1;
  unresolved conditions stay a single provisional case.
- Cartesian explosion is prevented structurally — each technique fires only on
  its own condition, decision-table rows come pre-enumerated from the analyzer,
  and bounds cap the rest.

## Alternatives considered

- **Option A (single intelligent prompt):** rejected — repeats the "model
  decides how much" non-determinism removed in Phases 21-22.
- **Option B (N specialised stages + merge):** rejected — over-engineered;
  multiplies stages/prompts/cost; heavy merge; conflicts with minimal-change.
- **Pure deterministic authoring (no LLM):** rejected — code can enumerate slots
  but cannot write concrete, document-grounded steps/data/expected-results.

## Limitations / future work

The planner reads variant values from condition `parameters`; if the analyzer
did not capture a documented dimension as a parameter, the planner cannot expand
it (it will not guess). Richer parameter extraction in the analyzer, and an
optional technique-coverage metric, are candidate follow-ups. Pairwise reduction
for 3+ interacting dimensions is deferred until a real case needs it — current
decision tables arrive pre-enumerated as combination conditions.
