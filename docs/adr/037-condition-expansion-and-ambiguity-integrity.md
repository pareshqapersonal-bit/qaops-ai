# ADR-037: Condition expansion and ambiguity integrity

**Status:** Accepted · **Date:** 2026-08-01 · **Relates to:** ADR-036 (test conditions), ADR-011/014/015 (LLM generates, code validates)

## Context — production evidence

Phase 21 deployed successfully (`bff25bd`). Testing the live system against a
small BOGO / cart-CTA PRD exposed a behavioural weakness, not a mechanical one.
Two runs produced a rigid pattern:

    scenario -> exactly one condition -> exactly one test case

One run: 5 requirements, 18 rules, 8 scenarios, **8 conditions, 8 cases, 0
unresolved, 100% condition coverage** — while the same artifact carried **5
requirement-analysis gaps**, including a blocker-severity eligibility ambiguity
and an undefined tag-copy string. Every condition was `explicit_requirement` or
`explicit_rule`; **zero** derived boundary/equivalence/combination conditions.

Two defects, established by reading the code:

1. **The analyzer never saw the gaps.** `TestConditionAnalyzer` passed only
   scenarios, requirements and rules to its prompt — `analysis.gap_report` was
   available in the data but never reached the model. So a known ambiguity could
   not influence resolved-vs-unresolved, and 100% condition coverage coexisted
   with a blocker gap.
2. **The scenario prompt pre-atomised.** `scenario_generator_v1.md` said "one
   scenario per distinct condition", so scenarios arrived already decomposed to
   one proposition each and the analyzer had nothing to decompose. The 1:1 was
   baked in upstream.

Contributing: the analyzer prompt under-drove derivation (a single-condition
example anchored the model to 1:1; no decision-table example; weak
resolved-vs-unresolved guidance).

## Legitimate 1:1 vs pathological 1:1

`scenario == condition` is **not** inherently a bug. When a scenario's evidence
has exactly one testable dimension, one condition is correct. It is a bug only
when the source documents additional meaningful dimensions (a decision table, a
documented boundary, a positive/negative pair) and the analyzer fails to derive
them, or when a known gap that blocks expected behaviour leaves every condition
`resolved`. Phase 22 targets the pathological case without inflating the
legitimate one.

## Decision

Extend `TestConditionAnalyzer` (no new stage, no model/ID/API/frontend
redesign):

### Gaps feed the analyzer, and a deterministic gap -> unresolved linkage

The gap report is now serialised into the analyzer prompt (`gaps_json`). More
importantly, a **deterministic** post-generation pass forces a condition
`UNRESOLVED` when a gap blocks its expected behaviour. A gap affects a condition
only when BOTH hold: the gap is tied to a requirement the condition tests
(`gap.requirement_id in condition.requirement_ids`), AND the gap's subject
matches the condition's subject (>= 2 shared significant tokens between gap text
and condition description/parameters). This keeps the linkage specific: a gap
about undefined tag *copy* unresolves a copy-checking condition but not a mere
visibility condition on the same requirement. Informational gaps with no
requirement link, and gaps whose subject does not match, are left alone — we do
NOT convert every gap into an unresolved condition. This is deterministic and
does not depend on the model obeying the prompt.

### Technique-driven derivation in the prompt

The prompt now instructs the model to list each scenario's documented dimensions
and produce one condition per materially distinct, independently testable one,
with a worked decision-table example (eligibility x mapping) showing one
scenario yielding several conditions. It also forbids fabricated expected
results (including the "confirm with product owner" anti-pattern) and sharpens
resolved-vs-unresolved.

### Category/behaviour contradiction guard

A deterministic check rejects a `negative` condition whose description asserts
the criteria ARE met (the COND-006 class observed in production) — the clear,
unambiguous self-contradiction only, not fuzzy semantic judgement.

### Minimal scenario-prompt change

"One scenario per distinct condition" is replaced with "each scenario is a
business/user behaviour that groups related situations for later decomposition".
This is the minimum needed for the condition architecture to function; broader
scenario-granularity tuning is deferred to Phase 22.1.

### Observability

A single deterministic log line per run records counts only — scenarios,
conditions, resolved/unresolved, derived/explicit, conditions-per-scenario
distribution, truncation — never prompts, secrets, or document content.

## Preserved (unchanged)

Evidence cross-link and derived-basis validation; canonical-signature dedup
(boundary variants survive by parameters); expansion bounds and
`expansion_truncated`; condition-coverage arithmetic (unresolved never counts as
covered; a provisional case does not confer coverage); all Phase 21 models, IDs,
entry points, API schemas, exports, and the frontend; and the entire
provider/execution architecture (Phases 19/20).

## Consequences

- One scenario can now yield several evidence-justified conditions; nothing is
  padded or invented.
- A gap affecting testable behaviour deterministically produces an unresolved
  condition, so condition coverage can no longer report 100% over a known
  blocking ambiguity.
- A legitimate single-dimension scenario still yields one condition.
- The improvement is robust to model behaviour because the gap linkage and the
  contradiction guard are deterministic, not prompt-only.

## Trade-offs and future work (Phase 22.1)

The gap-subject match is deterministic token overlap, not semantic — it can miss
a heavily paraphrased gap or, rarely, over-match; the requirement-link
precondition bounds the blast radius. This is deliberately **conservative on
failure direction**: when confidence is low the condition stays RESOLVED. A
false negative (a genuinely-blocked condition left resolved) is the accepted
failure — it keeps a testable condition rather than inventing an unresolved one —
whereas a false positive (an unrelated gap marking a condition unresolved and
understating coverage) is the failure we reject. Case 3 of the reviewed examples
is exactly this: same requirement, unrelated subject, zero overlap → stays
resolved, no false unresolved. We do not broaden the matching algorithm to chase
the missed paraphrases; embeddings, semantic search, fuzzy matching, or an extra
LLM pass were explicitly rejected to keep the linkage deterministic and
reviewable.

Scenario granularity is only minimally adjusted here; if production still shows
over-atomised scenarios after this change, Phase 22.1 will tune the scenario
generator directly. Test-case-per-condition expansion is intentionally left
unchanged until the condition layer is proven in production.
