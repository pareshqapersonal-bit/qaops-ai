# ADR-048: Test-case assumption provenance

**Status:** Accepted · **Date:** 2026-08-10 · **Phase 33** · **Relates to:** ADR-036 (evidence-bound conditions), ADR-045/046 (review layer)

## Context

`TestCondition` carries a rich evidence model (`source_basis`, `status`,
`rationale`, `parameters`, `gap_reference`) so every condition can answer "why does
this test exist?". That evidence stopped at the condition boundary: `TestCase` had
free-form `preconditions`, `test_data`, and `expected_result` with no way to
distinguish three different kinds of information a generated case contains:

1. SOURCE-BACKED behaviour (stated in the ticket/requirements/rules or an
   evidence-bound condition);
2. QA-GENERATED TEST DATA (representative values chosen to make a step executable -
   an OTP "123456", a sample SKU) - legitimate and desirable;
3. UNSUPPORTED ASSUMPTIONS (a product/system fact the case must rely on that the
   source never establishes - "the PDP shows a numeric review count", "the OTP
   expires in 30 seconds").

The Phase 32 Jira run surfaced the gap: a case could rest on an unstated product
fact (e.g. "a product with active reviews exists in the catalogue") with no way for
a reader to tell whether that was given or assumed. The goal is NOT to make cases
repeat the source, and NOT to forbid QA test data - only to stop assumptions from
silently appearing as facts.

## Decision

Add an explicit, optional provenance field for the third category, and strengthen
the generator contract - nothing else.

1. **`TestCase.assumptions: list[str]`** (default empty): product/system facts the
   case must assume that the source does not establish. QA test data stays in
   `test_data`; evidence-backed behaviour remains traceable via the condition's
   `source_basis`. The field is additive and defaulted, so with `exclude_defaults`
   serialization an evidence-complete case is byte-identical to pre-Phase-33 output
   (pinned by a regression test).
2. **Generator prompt contract** (`test_case_generator_v1.md`, edited in place -
   additive guidance only): names the three categories; keeps chosen values in
   `test_data` and forbids phrasing them as product rules ("Enter an OTP such as
   123456" is fine; "OTPs are always 6 digits" is not, unless the evidence says so);
   and directs any required-but-unsupported fact into `assumptions` rather than
   `preconditions`/`expected_result`.
3. **Threading** (`schemas.py` wire model + `test_cases.py`): the field flows from
   the LLM extraction to the `TestCase`, defaulting empty when absent.

### Boundaries

- Requirements, business rules, gaps, scenarios, and the `TestCondition` evidence
  model are unchanged.
- `CoverageValidator` does not read `assumptions` (it never read `test_data`/
  `preconditions`/`expected_result`); coverage is unaffected.
- `QualityReviewer` and `ReviewAgent` do NOT consume or surface `assumptions` in
  this phase - the field is populated but not yet read downstream. A future phase
  may have the reviewer surface it.
- The generator's control flow (slots, IDs, provisional inheritance, bounds) is
  unchanged and remains deterministic; provisional/unresolved handling is untouched.

## Consequences

- Unsupported assumptions become visible and separable instead of silent, without
  prohibiting legitimate QA test data or requiring source-literal values.
- Existing document runs are byte-identical (proven, not assumed): an
  evidence-complete case serializes with no `assumptions` key.
- The provenance is available for a future reviewer/exporter to consume.

## Alternatives considered

- **Prompt-only, no schema:** rejected - gives no structural provenance; a reader/
  reviewer still can't separate assumptions from data.
- **Per-value provenance on every test_data/precondition:** rejected - large schema
  and prompt burden, high regression risk, disproportionate to the problem.
- **A new provenance stage/agent:** rejected - violates "strengthen the generator
  over adding a pipeline/agent"; a downstream stage cannot see evidence it wasn't
  given.

## Limitations / future work

Enforcement is soft (the model may still occasionally embed an assumption in prose),
but the field makes most cases separable - strictly better than no separation.
Having the QualityReviewer surface assumption-heavy cases is deferred.
