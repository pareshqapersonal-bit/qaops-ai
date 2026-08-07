# ADR-044: Narrow gap propagation in unresolved classification

**Status:** Accepted · **Date:** 2026-08-07 · **Relates to:** ADR-036 (exhaustive test design), ADR-037 (condition expansion & ambiguity integrity)

## Context

When the pipeline cannot determine a condition's expected outcome from the
evidence, it correctly marks the condition `unresolved`, tracks it as a gap, and
the test case's `expected_result` says the behaviour must be confirmed. This is
intended, evidence-honest behaviour.

A real failing run exposed a defect: the "Auto-Delete Test Customer Mobile
Numbers" PRD produced **20 of 22 conditions unresolved**, and nearly every test
case carried a "confirm with the product owner" placeholder - even for conditions
whose expected behaviour was fully specified (an admin adding a number, numbers
persisting across sessions, the cron running nightly, deletion targeting only
Eyewear data). A healthy baseline run (the "BOGO Offer" PRD) produced a correct
4 of 11 unresolved.

## Root cause (proven from run artifacts)

Tracing the failing run's artifacts (requirements -> business rules -> gaps ->
conditions -> `gap_reference`) showed the mechanism precisely:

- The GapAnalyzer produced 7 legitimate but **requirement-level** gaps (mobile
  number format undefined, deletion strategy undefined, cron timezone undefined,
  etc.).
- The TestConditionAnalyzer's rule was effectively "if a gap exists on this
  condition's requirement, the condition is unresolved." So each gap fanned out
  across **every** condition sharing its requirement - including conditions that
  verify a **different, fully-specified aspect** of that requirement. A gap about
  mobile-number *format* unresolved the conditions for *adding*, *persisting*,
  and *removing* a number; a gap about the cron *timezone* unresolved the
  conditions for the job running *nightly* and *excluding unconfigured numbers*.

The 20/22 was the model's direct output, driven by the over-broad prompt rule.
The deterministic gap-linkage backstop (`_apply_gap_linkage`) shared the same
flaw: its subject-overlap test matched on generic domain nouns
("mobile"/"number"/"customer") that appear in nearly every condition, so it too
would re-block specified conditions.

This refuted the earlier hypotheses: it is **not** a single SSO gap propagating,
**not** backend actions being inherently unresolved (backend/derived conditions
resolve fine when no gap touches their subject), but **over-broad gap
propagation** - a gap poisoning a whole requirement rather than the specific
aspect it concerns.

## Decision

Narrow gap propagation to **subject-matter overlap** in the analyzer **prompt
only**, keeping the change to the smallest surface supported by production
evidence. The 20/22 failure was the LLM's direct classification output, so the
prompt is where the fix belongs.

### Prompt (the fix)

The TestConditionAnalyzer prompt now instructs: a listed gap makes a condition
`unresolved` **only when the gap's missing information is the very thing that
condition verifies**. Sharing a requirement with a gap is explicitly not
sufficient; a condition verifying a different, fully-specified aspect stays
`resolved`. Worked examples cover tag-copy vs tag-visibility, number-format vs
add/persist/remove, and cron-timezone vs runs-nightly. A guard rail is added so
narrowing does not become ignoring: if the missing information directly affects
the expected result the condition verifies, it MUST stay `unresolved` - narrowing
must never fabricate an unsupported outcome.

### Deterministic backstop: deliberately unchanged (for now)

`_apply_gap_linkage` / `_blocking_gap` are left exactly as they were. The
production failure originated in the LLM's classification, not the deterministic
backstop (which only ever *adds* unresolved to conditions the model marked
resolved). Changing it now would be speculative. The correct next step is to
re-run the Auto-Delete PRD with the prompt fix and observe the real output; only
if the backstop is then shown to still propagate gaps incorrectly will a targeted
change to it be considered. This keeps the change minimal and evidence-driven.

## Verification

- Root cause traced from the two real run artifacts (checked into
  `tests/fixtures/phase29`): the failing Auto-Delete result (20/22 unresolved)
  and the healthy BOGO result (4/11).
- The prompt fix is a reasoning change; its effect is validated by **re-running
  the Auto-Delete PRD on the live LLM** and observing that fully-specified
  conditions now resolve while genuinely gap-affected ones (number format, cron
  timezone) stay unresolved, with BOGO unchanged.
- Unit tests pin the prompt contract (the narrowing instruction, the anti-ignore
  guard rail, never-fabricate, and evidence-first are all present) and retain the
  two artifacts as the baseline for that re-run comparison.

## Consequences

- Fewer false-positive `unresolved` classifications: a gap no longer poisons
  fully-specified sibling conditions.
- Genuinely ambiguous conditions still become `unresolved` -> gap -> provisional
  case, unchanged.
- No interface change: no new API field, no schema change, no UI change.

## Alternatives considered

- **Downstream reclassification of unresolved conditions:** rejected - would risk
  fabricating expected results the evidence does not support, violating the
  evidence-first philosophy. The fix improves the analyzer's reasoning instead of
  repairing its output.
- **Tuning the lexical overlap threshold further:** rejected as overfitting - the
  failing PRD's gaps and conditions share genuine vocabulary that no token
  threshold cleanly separates. Semantic discrimination is the prompt's job.
- **Placeholder-text canonicalization:** deferred; cosmetic, not correctness.

## Limitations / future work

The deterministic backstop (`_blocking_gap`) is unchanged and remains lexical: in
principle it could still re-block a resolved condition on coarse token overlap. It
is left as-is deliberately - the production evidence points at the LLM, and we
change the smallest surface. If a post-fix re-run shows the backstop still
propagating gaps incorrectly, a targeted change to it (e.g. excluding
domain-ubiquitous tokens from the overlap) becomes the evidence-supported next
step. Placeholder-text canonicalization remains candidate future work.
