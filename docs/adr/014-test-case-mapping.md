# ADR-014: Flat test-case wire schema, code-assigned step numbers, per-scenario reference scoping

**Status:** Accepted · **Date:** 2026-07-19 · **Relates to:** ADR-001, ADR-011, ADR-012

## Context

Test cases differ from earlier generated artifacts in three ways that force
mapping decisions: a scenario may yield several cases, each case has ordered
steps, and each case references both a scenario and a subset of that
scenario's requirements. Each needs a rule that keeps ADR-001 (code owns IDs
and structure) intact.

## Decision

1. **Flat wire schema, not grouped.** Each `ExtractedTestCase` carries its own
   `scenario_id` reference rather than being nested under a scenario object.
   The flat shape maps directly onto the existing `TestDesignResult`
   (scenarios + test_cases + coverage), so Phase 5's coverage validator slots
   in without a new aggregate. A grouped/nested schema was considered and
   rejected: it would have required a parallel result model for no downstream
   benefit.

2. **Step numbers are assigned by code from list order.** The wire step
   (`ExtractedTestStep`) has no `number` field; the model supplies steps as an
   ordered array and stage code numbers them 1..N via `enumerate`. A
   mis-numbered or gapped sequence therefore cannot exist. The domain model's
   1..N validator remains as defense in depth, but it can never fire from
   model output — the ID/structure-from-code principle applied to ordering.

3. **Requirement references are scoped to the case's own scenario.** Verifying
   a case's `requirement_ids` against the global requirement set is
   insufficient: a case under SC-002 could cite REQ-001 (a real requirement,
   but one linked only to SC-007) and pass. Stage code instead checks each
   case's requirements are a subset of *its* scenario's requirement links,
   raising `StageError` on any stray reference. This catches a class of
   hallucinated cross-links a global check misses, and it is only possible
   because the scenario→requirement links are already known from Phase 3.

4. **Duplicate policy is reused, not re-invented.** Exact duplicates (same
   scenario + casefolded, whitespace-normalized title) fail loudly, per
   ADR-012. Near-duplicate flagging remains Phase 5's Deduplicator.

## Consequences

- Full traceability closure is enforced structurally: every test case resolves
  to a real scenario and to requirements genuinely linked to that scenario.
- Reusing `TestDesignResult` keeps the model surface small and Phase 5 clean.
- Cost: the per-scenario subset check assumes scenarios were produced by the
  Phase 3 stage (whose links are trusted). Accepted — that is the only
  supported way scenarios enter this stage.
