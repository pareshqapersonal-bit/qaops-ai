# ADR-045: Deterministic Quality Review layer (QualityReviewer)

**Status:** Accepted · **Date:** 2026-08-07 · **Phase 30** · **Relates to:** ADR-015 (deterministic coverage), ADR-041 (agent abstraction), ADR-043 (multi-agent supervisor)

## Context

Phases 25-29 established: the deterministic pipeline is the sole artifact author;
agents are advisory and never mutate artifacts; existing behaviour stays
byte-identical; no feedback loops into generation. A run produces a
`TestDesignResult` whose `CoverageReport` already computes, deterministically,
coverage per requirement/scenario/condition/business-rule, a traceability matrix,
duplicate detection, and invalid-reference detection.

Phase 30 introduces an independent quality review of the completed output. The
question was whether to build an LLM `ReviewAgent` immediately or a deterministic
review layer first. Given that the substance of a quality review (coverage gaps,
unresolved ratios, duplicates, broken references) is already computed
deterministically, an LLM-first agent would either duplicate that work or wrap
deterministic facts in nondeterministic prose.

## Decision

Introduce a deterministic **`QualityReviewer`** (Phase 30) and defer the LLM
`ReviewAgent` to a future phase.

### Separation of concerns

```
CoverageValidator -> TestDesignResult.coverage (CoverageReport)  [deterministic, exists]
                          | (read-only consume)
QualityReviewer   -> ReviewReport (findings)                     [deterministic, NEW]
                          | (future, read-only consume)
ReviewAgent       -> narrative/explanations over ReviewReport    [LLM, advisory, FUTURE]
```

Deterministic computation and future AI reasoning are split at the `ReviewReport`
boundary: the reviewer computes findings; the future agent will only explain them.

### QualityReviewer

- **Not an Agent.** It has no LLM (its constructor takes no client, so "no LLM"
  is enforced by type signature, exactly as `CoverageValidator` enforces it), and
  it makes no decisions. It is a pipeline-adjacent deterministic analyzer in
  `qaops/review/`, not in `qaops/agent/`.
- **Read-only, pure.** `review(result: TestDesignResult) -> ReviewReport`. Same
  input -> same output. It never mutates the result, generates no artifact,
  invokes no stage/loop, and writes no checkpoint.
- **Consumes `CoverageReport`; never recomputes coverage.** It reads the existing
  metrics, `uncovered_*` helpers, `duplicate_pairs`, and `invalid_references`,
  and adds only net-new interpretive checks (empty scenarios, provisional cases,
  truncation, unresolved-ratio thresholds).
- **Advisory only.** Findings carry a severity (info/warning/critical) and
  category (coverage/ambiguity/duplication/references/completeness) but never
  gate, fail, or downgrade a run. A run with CRITICAL findings is still COMPLETED.

### Invocation

The **Runner** invokes the reviewer, on **COMPLETED runs only**, after the
`SupervisorAgent` returns. The Phase 28 supervisor architecture is unchanged - the
reviewer sits outside it. Any failure inside review degrades to "no review"
without affecting the COMPLETED status, mirroring how the runner already tolerates
reflection/loop-summary serialization failures.

### New artifact and surfacing

A `ReviewReport` (findings + observations + recommendations). Surfaced
**additively**: an optional `review` field on the run status response (defaulted
to `None`, so existing clients are unaffected) and a standalone
`review_report.json` export listed among the run's artifacts. It is never merged
into `TestDesignResult` or `CoverageReport`; findings reference artifact ids but
copy no artifact content.

## Amendment (Phase 30 v2): deeper deterministic findings

The initial reviewer reported only high-level metrics. v2 deepens it with five
additional deterministic findings, each reading already-computed data and never
recomputing coverage, keeping the architecture unchanged (same reviewer, same
`ReviewReport`, additive only):

- **Blocker/major gaps** (`blocker_gaps_present` CRITICAL, `major_gaps_present`
  WARNING) - reads `gap_report.gaps[].severity`, which `CoverageValidator` never
  touches.
- **Partial requirements** (`partial_requirements` WARNING) - consumes
  `RequirementCoverage.status == PARTIAL` and its already-computed
  `missing_categories`.
- **High provisional ratio** (`high_provisional_ratio` WARNING) - a thresholded
  ratio complementing the existing provisional count.
- **Priority distribution skew** (`priority_distribution_skew` WARNING,
  `QUALITY`) - reads `TestCase.priority`; flags a suite with no critical/high.
- **Type balance** (`missing_negative_boundary_coverage` WARNING, `QUALITY`) -
  reads `TestCase.test_type`; flags a positive-only suite.

Priority and test-type distributions are also added as neutral observations.
Only the `QUALITY` `ReviewCategory` member is new. A proposed sixth check
("traceability holes") was **deliberately dropped**: defined as empty-trace it
duplicates `uncovered_requirements`, and defined as missing-from-entries it can
never fire on a well-formed result - so it would be a duplicate or dead code.

No new stage, agent, API field, or schema change: new finding codes and the new
category serialize through the existing string-typed API fields additively.

## Consequences

- Objective, reproducible quality findings on every completed run, with the
  failing Auto-Delete baseline flagged CRITICAL (20/22 unresolved) and the healthy
  BOGO baseline INFO (4/11) - discrimination validated by tests.
- Determinism and all Phases 25-29 guarantees preserved: pure read-only function,
  no execution/checkpoint/loop/supervisor/analyzer surface touched, byte-identical
  opt-out (absent field when no review).
- A stable `ReviewReport` contract for the future LLM `ReviewAgent` to consume.

## Alternatives considered

- **LLM ReviewAgent first:** rejected - it would duplicate CoverageValidator or
  wrap deterministic facts in nondeterministic prose, and would define the review
  contract around nondeterministic output.
- **Reviewer recomputing coverage:** rejected - duplicates CoverageValidator;
  the reviewer consumes `CoverageReport` instead.
- **Gating runs on review findings:** rejected - the reviewer is advisory; the
  pipeline's own validators remain the only hard gates.

## Limitations / future work

The reviewer is deterministic and lexical-free by design; nuanced natural-language
judgements are deferred to the future LLM `ReviewAgent`, which will consume this
`ReviewReport`. Phase 30 triggers on COMPLETED runs only; reviewing
PARTIALLY_COMPLETED runs is possible future work.
