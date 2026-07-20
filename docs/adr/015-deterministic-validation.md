# ADR-015: The validation stage is deterministic, with no LLM in its signature

**Status:** Accepted · **Date:** 2026-07-19 · **Relates to:** ADR-001, ADR-007, ADR-012

## Context

ADR-001 split the system: the LLM generates, deterministic code validates.
Phases 2-4 built the generation half. Phase 5 builds the validation half -
the first stage whose entire job is to check, measure, and report on what the
model produced. Two questions needed deciding: how strongly to enforce "no LLM
calls", and where coverage data lives.

## Decision

1. **The zero-LLM guarantee is in the type signature.** Every generation stage
   takes `(client, prompts, settings)`. `CoverageValidator.__init__` takes
   **nothing**. It cannot make an LLM call because it holds no client - the
   guarantee is structural, not a comment. A reviewer confirms it by reading
   the constructor; a grep for `.complete(` / `generate_structured` /
   `PromptLoader` in `coverage.py` returns only the docstring.

2. **Coverage lives in the existing `TestDesignResult.coverage` field.** Phase
   4 deliberately left `coverage` at its default for this stage to fill. The
   validator returns `data.model_copy(update={"coverage": report})` - the
   input is never mutated, and no parallel aggregate model is introduced.

3. **`CoverageReport` is extended additively.** Phase 0 defined it before the
   full validation scope was known (it had only requirement coverage,
   traceability, and flat duplicate pairs). Phase 5 adds business-rule
   coverage, scenario coverage, aggregate metrics, structured duplicate pairs
   with reasons, and invalid-reference reporting - all as optional fields with
   defaults, so `CoverageReport()` and the Phase 4 pass-through stay valid.
   The legacy `suspected_duplicates` field is kept and mirrored from
   `duplicate_pairs` for backward compatibility.

4. **Coverage semantics, defined:**
   - A **requirement** is *covered* if a test case references it and every
     scenario category applicable to it has a case; *partial* if it has cases
     but is missing a category; *uncovered* if it has no case.
   - A **business rule** is covered *transitively* - it has no direct
     test-case link, so it is covered when its requirement is covered.
   - A **scenario** is covered if any test case references it.
   - **Duplicate detection** flags, never deletes (ADR-007): identical
     normalized titles, or same-scenario + same-requirements + title token
     overlap >= 0.7. This complements the generation-time exact-duplicate
     rejection of ADR-012; it does not replace it.
   - **Invalid references** (a case pointing at an absent SC/REQ) are
     reported, not trusted away. Upstream stages already reject these, so a
     non-empty list is a defect report, not a normal outcome - but a validator
     that assumes its input is clean is not a validator.

## Consequences

- The "code validates" half of ADR-001 now has a concrete, testable home, and
  its determinism is provable (repeated runs are byte-identical; tests need no
  MockLLMClient for the stage itself).
- Coverage percentages and gap lists give the tool a QA-review voice beyond
  raw generation - the interview-facing payoff of the whole architecture.
- Cost: the additive `CoverageReport` growth means two duplicate
  representations (`duplicate_pairs` and legacy `suspected_duplicates`) until a
  1.0 breaking window removes the latter. Accepted for backward compatibility.
