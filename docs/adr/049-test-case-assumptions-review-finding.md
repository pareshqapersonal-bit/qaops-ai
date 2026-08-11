# ADR-049: Test-case assumptions review finding

**Status:** Accepted · **Date:** 2026-08-10 · **Phase 34** · **Relates to:** ADR-045 (QualityReviewer), ADR-046 (ReviewAgent), ADR-048 (TestCase.assumptions)

## Context

Phase 33 (ADR-048) added `TestCase.assumptions` - unsupported product/system facts
a case must rely on that the source does not establish. But nothing consumed it: a
codebase trace confirmed `assumptions` was written by the generator and read by no
one. In particular the QualityReviewer already iterates `result.test_cases` but
never inspected `tc.assumptions`, so the data died at the review boundary and never
reached `review_advice.json`, where a QA lead would act on it.

Phase 34 makes assumptions actionable on the review surface, without unsafe
interpretation of the free-text strings.

## Decision

Add a single, deterministic, threshold-gated finding to the QualityReviewer.

### Why assumptions are NOT classified by prose

The five categories a reader might want (environment/setup, test-data availability,
product capability, business-rule/behaviour, other) live only in the natural-language
meaning of each string; the artifact carries no structured type/severity per
assumption (`assumptions` is a flat `list[str]`). Distinguishing "a SKU exists in the
environment" (setup) from "the OTP expires after 5 minutes" (business rule) would
require inferring category from wording. That is unsafe: it is non-deterministic in
spirit (a rewording flips the class), and it repeats a failure mode this codebase has
already rejected (the keyword/token heuristics dropped in Phases 29-30). So the
reviewer treats each assumption as an opaque string and never categorizes it.

### Why a quantity-based threshold

Because per-assumption severity cannot be derived safely from text, severity is
derived from quantity instead: "a material share of the suite depends on unconfirmed
facts" is a defensible signal regardless of any individual assumption's kind. This
mirrors the existing `high_provisional_ratio` finding, which is also a ratio-threshold
signal. The threshold is the justification for the severity - not the prose.

### The finding

- **code**: `test_case_assumptions`
- **severity**: `WARNING` (assumptions are "confirm this", not "broken" - they should
  influence handoff readiness via the existing warning mechanism, never assert the
  suite is invalid)
- **category**: `completeness` (reused; no new ReviewCategory member)
- **threshold**: fires when `cases_with_>=1_assumption / total_test_cases >= 0.50`
  (`_ASSUMPTION_WARNING_RATIO`), matching the provisional-ratio constant's spirit;
  below the threshold no finding is emitted
- **references**: the exact `TestCase` IDs of every case carrying at least one
  assumption, sorted, verbatim - so QA can trace each case to its `assumptions[]`
- **message**: the count and percentage; the assumption text itself is never echoed
  or interpreted in the finding

### Readiness impact

Only in aggregate, and only when the finding fires: it participates in the
ReviewAgent's existing warning count in the ReviewAdvice headline, exactly like every
other warning finding. Below threshold there is no readiness impact. No new readiness
logic is added.

### Unchanged by construction

- **ReviewAgent**: unchanged. It is finding-agnostic - it sorts and copies whatever
  findings exist, references verbatim, so the new finding reaches ReviewAdvice
  automatically. The Phase 31 boundary (agent never invents/alters findings) holds.
- **CoverageValidator**: unchanged. Assumptions are a confidence concern, not a
  traceability one; coverage never reads them and stays byte-identical.
- **Phase 33**: unchanged. The `TestCase.assumptions` field, wire schema, generator,
  and prompt are consumed, never modified. `provisional` status is untouched -
  assumptions never flip it.

## Consequences

- Assumption load becomes visible and actionable on the QA-lead surface, with exact
  case-ID traceability, without fabricating a per-assumption classification.
- Runs with no assumptions are byte-identical to pre-Phase-34 output (the finding is
  absent), pinned by a regression test.
- Severity is quantity-justified and deterministic; no LLM and no prose heuristic is
  used anywhere in the finding.

## Calibration caveat

The 50% threshold was exercised through the real, unmodified pipeline
(RequirementAnalyzer -> ... -> TestCaseGenerator -> QualityReviewer): a ticket-sourced
run with 2 of 3 cases carrying assumptions (67%) produced the expected WARNING with
references [TC-001, TC-002]. Because this environment has no live LLM API key, the
assumption STRINGS in that calibration were supplied as realistic sparse-ticket text
rather than authored by a live model; the finding mechanics, ratio computation, and
references are genuine pipeline output. A fully autonomous live-LLM calibration across
many runs remains future validation; 50% is accepted as a conservative default,
mirroring `high_provisional_ratio`, and is a single documented constant that is
trivially tunable.

## Alternatives considered

- **Observation only**: rejected - observations do not reach `review_advice.json`
  (the ReviewAgent does not read them), so QA would not see it where they act.
- **Always-fire finding (no threshold)**: rejected - over-signals on suites where a
  single benign setup assumption is routine.
- **Per-assumption severity/category**: rejected - unsafe from free text; would
  require a Phase 33 schema change (structured assumptions), which is out of scope.
- **LLM classification of assumptions**: rejected - no demonstrated need; deterministic
  counting suffices.
