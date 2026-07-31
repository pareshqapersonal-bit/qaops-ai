# ADR-036: Exhaustive, evidence-bound test design via test conditions

**Status:** Accepted · **Date:** 2026-07-31 · **Relates to:** ADR-001/011/014 (LLM generates, code validates), ADR-007 (flag, never delete), ADR-015 (deterministic coverage)

## Context

Before Phase 21 the pipeline produced roughly one test case per scenario. The
audit established this was not a structural cap but a prompt-and-incentive
artifact: the test-case schema already allowed many cases per scenario, but the
scenario stage encoded "one scenario per distinct condition" and the case prompt
said only "one or more," with no technique-driven expansion and a single
all-scenarios-in-one-call generation that biased toward minimal output. Coverage
measured *presence* (a scenario with >=1 case was "covered"), so 1:1 trivially
reported "100%". The result under-tested: boundaries, equivalence classes,
negative paths, and rule combinations that the requirements clearly justified
were collapsed into a single case.

## Decision

Insert one new stage — the **TestConditionAnalyzer** — between scenario
generation and test-case generation, introducing a first-class **TestCondition**
between `Scenario` and `TestCase`. The chain becomes REQ -> BR -> SC -> COND -> TC.

    ... -> scenarios -> test_condition_analyzer -> test_case_generator -> coverage

### TestCondition

A condition is a single testable proposition carrying its evidence: `category`
(technique), `source_basis` (evidence type), the referenced requirement/rule/
scenario IDs, `parameters` (the dimension values that make it distinct), a
`status` (resolved/unresolved), and an optional `gap_reference`. IDs (`COND-*`)
are assigned by code, never the model.

### Evidence binding, not invention

The analyzer enforces deterministically that every condition cites evidence, and
that a *derived* basis (boundary, equivalence, documented combination/state
transition) references a rule or requirement that actually carries the numeric
limit, class, or state. A condition with a derived basis and no cited rule is
rejected loudly. This is what lets "BOGO applies at quantity >= 2" legitimately
produce boundary conditions at 1/2/3 while a silent, undocumented behaviour
produces nothing — the model cannot conjure expected behaviour the documents do
not define.

### Condition-driven, no fixed ratio

The test-case generator consumes conditions and produces one or more cases per
condition, only when distinct data, boundaries, or states require separate
execution. There is no scenario:case or condition:case ratio. Counts are
emergent from the evidence.

### Ambiguity becomes a gap, never a guess

When expected behaviour cannot be established, the condition is preserved with
`status=unresolved` and a `gap_reference`; the analyzer synthesizes a gap (merged
into the existing gap report, deduplicated by normalized text) and any resulting
test case is marked `provisional`. Unresolved conditions are never counted as
covered.

### Deterministic dedup that preserves boundary variants

Conditions and cases are de-duplicated by a canonical signature built from
meaning-bearing fields including `parameters` / `test_data` and expected result —
so quantity=2 and quantity=3 have different signatures and both survive, while a
pure restatement collapses. Near-duplicate *flagging* remains flag-never-delete
(ADR-007). Duplicate cases are dropped deterministically rather than raising.

### Bounded expansion, truncation is visible

`max_conditions_per_scenario`, `max_cases_per_condition`, and
`max_total_test_cases` cap expansion. Hitting a bound sets `expansion_truncated`
and a human-readable note that flows into coverage and the API/UI, so a
truncated run is reported as a floor — never silently as "100%".

### Multi-dimensional, honest coverage

Coverage now reports requirement, business-rule, scenario, and **condition**
dimensions. A resolved condition is covered only when it has >=1 non-provisional
case. The single headline percent is retained for backward compatibility and
equals requirement coverage; the UI and docs state plainly that coverage
measures how much of what was identified has a test, not that testing is
exhaustive.

### Division of labour (unchanged philosophy)

The LLM interprets requirements, identifies conditions, and proposes
boundary/equivalence values. Code assigns IDs, validates evidence and
references, computes signatures and dedup, enforces bounds, and computes
coverage — the same "LLM generates, code validates" split as the rest of the
pipeline (ADR-001/015). The provider/execution architecture (Phases 19/20) is
untouched.

## Backward compatibility

- `TestCase.condition_id` and `provisional` are optional with defaults, so
  pre-Phase-21 artifacts remain schema-valid.
- The SCENARIOS entry point now runs `conditions -> cases -> coverage`;
  conditions are derived from the supplied scenarios, so scenario-file input
  still works without fabricating requirements or rules.
- The API keeps `coverage_percent`; new fields are additive.

## Consequences

- One scenario can yield many evidence-justified cases; nothing is padded and
  nothing is invented.
- Every test case traces to a condition that answers "why does this exist?".
- 100% requirement coverage can coexist with incomplete condition coverage; the
  report shows both rather than a single misleading number.
- Two new LLM calls at most per run shape (analyzer + generator remain single
  calls); expansion stays inside explicit bounds.
