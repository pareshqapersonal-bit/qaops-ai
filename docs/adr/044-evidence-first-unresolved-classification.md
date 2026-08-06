# ADR-044: Evidence-first unresolved classification and justification gate

**Status:** Accepted · **Date:** 2026-08-06 · **Relates to:** ADR-036 (exhaustive test design), ADR-037 (condition expansion & ambiguity integrity)

## Context

When the pipeline cannot determine a condition's expected outcome from the
available evidence, it correctly refuses to fabricate one: the condition is
marked `unresolved`, becomes a tracked gap (ADR-036/037), and the corresponding
test case's `expected_result` states that the behaviour must be confirmed with
the product owner. This is intended, evidence-honest behaviour — fabricating a
pass/fail assertion for undocumented behaviour would be worse.

The observed quality problem is not the placeholders themselves but
**false-positive unresolved classifications**: the `TestConditionAnalyzer`
sometimes marks a condition `unresolved` even though the stated requirements and
business rules *do* support a definitive expected result. The model reaches for
`unresolved` conservatively instead of exhausting the evidence first. The result
is a "confirm with the PO" placeholder where a determinate, evidence-based answer
was derivable.

Phase 29's objective is to reduce those false positives while preserving the
evidence-first philosophy — recommend clarification only when the available
requirements genuinely do not support a definitive conclusion.

## Decision

Two additive changes, no architectural redesign. Every Phase 25–28 guarantee is
preserved: the pipeline remains the sole artifact generator, `AdaptiveExecutor`
owns retry/failover, `CheckpointStore` owns checkpoints, `DesignService` owns
execution, and the SupervisorAgent architecture is untouched.

### 1. Evidence-first prompt (TestConditionAnalyzer)

The "Resolved vs unresolved" section of `test_condition_analyzer_v1.md` is
strengthened to require the model to exhaust the evidence before classifying a
condition as `unresolved`:

- Before marking `unresolved`, the model must actively search ALL analyzed
  requirements AND all business rules — not only those linked to the scenario —
  for a stated outcome, limit, rule, or transition that determines the expected
  behaviour, including legitimately derived boundaries/equivalences/combinations/
  transitions.
- Default to `resolved` whenever the evidence supports a definitive outcome;
  reach for `unresolved` only after that search genuinely yields nothing.
  Conservatism is explicitly not a reason to mark `unresolved`.
- Every `unresolved` condition must carry a specific, substantive `gap_reference`
  naming the exact missing information. A vague or empty gap_reference is a signal
  the condition was probably resolvable.

The pre-existing never-fabricate rule is kept verbatim: genuinely unsupported
behaviour must remain `unresolved`, and the model must not invent an expected
result or write "confirm with the product owner" as though it were real product
behaviour.

### 2. Deterministic justification gate (report-only)

A deterministic check in `conditions.py` (`_report_unjustified_unresolved`) runs
after the existing gap→unresolved linkage. For every condition marked
`unresolved`, it verifies the `gap_reference` is a substantive justification —
non-empty, long enough to state a specific open question, and not a bare
"confirm with the PO/BA/client" (or `TBD`/`unknown`/`n/a`) placeholder that names
who to ask but not what is missing.

Crucially, the gate **detects and reports only** — it never reclassifies. The
model remains responsible for the classification; the gate surfaces likely
false-positive `unresolved` conditions (by id and count, via the module logger,
using non-sensitive diagnostics only). This keeps the pipeline the sole author of
the classification and makes the "unresolved" state auditable rather than an
unchecked model assertion.

## Why report-only, not auto-reclassify

Automatically flipping an `unresolved` condition to `resolved` during validation
would require the pipeline to deterministically judge that the evidence supports a
specific expected outcome — a judgement that belongs to the model reasoning over
the evidence, not to a keyword rule. Reclassifying risks fabricating a resolution
the evidence may not actually support, the exact harm ADR-036 prevents. Detection
plus the strengthened prompt raises quality without that risk; the gate is the
backstop that surfaces residual false positives for review.

## Consequences

- Fewer false-positive `unresolved` classifications, so fewer "confirm with the
  PO" placeholders where the evidence actually supported a determinate answer.
- Genuinely unsupported behaviour still becomes `unresolved` → gap →
  provisional case, unchanged.
- `unresolved` is now evidence-bound and auditable: unjustified classifications
  are logged for observability.
- No interface change: no new API field, no schema change, no UI change; the
  deterministic pipeline's external behaviour for well-formed inputs is unchanged
  except for the intended quality improvement in classification.

## Alternatives considered

- **Auto-reclassify unjustified unresolveds:** rejected — see above; risks
  fabricated resolutions.
- **New API/schema field to surface the report:** rejected for Phase 29 — the
  objective is artifact quality, and the log-based report satisfies "detect and
  report" without a schema change. A structured surface can be added later if
  needed.
- **Canonicalizing the placeholder text:** deferred to a later
  presentation-polish release; it is cosmetic, not a correctness improvement.

## Limitations / future work

The justification gate is a conservative textual check; it flags obviously
unjustified classifications, not every questionable one. Placeholder-text
canonicalization and a structured (non-log) surface for the report remain
candidate future work.
