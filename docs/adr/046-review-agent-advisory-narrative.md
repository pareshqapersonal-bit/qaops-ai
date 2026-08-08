# ADR-046: ReviewAgent - advisory narrative over ReviewReport

**Status:** Accepted · **Date:** 2026-08-07 · **Phase 31** · **Relates to:** ADR-041 (agent abstraction), ADR-043 (multi-agent supervisor), ADR-045 (deterministic QualityReviewer)

## Context

ADR-045 introduced the deterministic `QualityReviewer`, which consumes a
completed run's `CoverageReport` and produces a `ReviewReport` of objective
findings. It explicitly reserved a future seam: an LLM `ReviewAgent` that would
consume the `ReviewReport` to explain and recommend - reading findings, never
recomputing them.

After Phases 25-30 the system computes rich quality findings but has no agent that
reasons about them or explains them for a QA lead deciding whether a pack is ready
for a client. This is the first phase to deliberately introduce non-deterministic
(LLM) output into the product, so it must be strictly opt-in.

## Decision

Introduce a `ReviewAgent` (subclass of the `Agent` ABC) that consumes the
`ReviewReport` and produces advisory `ReviewAdvice`: prioritized, plain-language
explanations of the findings plus consolidated recommendations.

### Boundaries (hard)

- **Consumes the `ReviewReport` only** - not the `TestDesignResult`, not metrics -
  so it structurally cannot recompute anything. The `ReviewReport` remains
  authoritative.
- Never creates findings, changes a finding's severity or references, mutates
  artifacts, affects pipeline execution, affects loop decisions, or feeds advice
  back into generation.

### Invocation & gating

- **Runner-invoked**, COMPLETED runs only, after the `QualityReviewer` produces
  the `ReviewReport`, on both the fresh and resume-completed paths. The Phase 28
  `SupervisorAgent` is unchanged.
- **Setting-gated, OFF by default** (`review_advice_enabled=False`). Because the
  narrative may use an LLM and is non-deterministic, runs stay byte-identical
  unless explicitly enabled. With it disabled, `_build_review_advice` returns
  nothing - no field, no export - so behaviour is byte-identical to Phase 30.

### Determinism & fallback

- `advise()` always builds a complete `ReviewAdvice` deterministically from the
  report: findings prioritized CRITICAL -> WARNING -> INFO (stable secondary sort),
  each item echoing the finding's code/severity/references; consolidated,
  de-duplicated recommendations; a severity-count headline.
- An optional best-effort LLM pass may refine ONLY the free-text prose (headline
  and per-item explanation), mapping refinements back by finding `code`. Unknown
  codes are ignored; severity and references are never changed. Any failure or
  unusable output falls back to the deterministic advice.
- `generated_by` records provenance (`"deterministic"` | `"llm"`) for trust
  calibration.

### Surfacing (additive)

- New models `ReviewAdvice` / `ReviewAdviceItem`.
- Optional `review_advice` field on the run-status response (defaulted `None`,
  backward compatible) and a standalone `review_advice.json` export.
- New finding severities/categories are unaffected; the advice reuses the
  existing `ReviewSeverity`. New prompt `agent_review_advice_v1.md`.

## Consequences

- A QA lead gets a prioritized, readable explanation of the deterministic
  findings, opt-in, without any risk to reproducible runs by default.
- The `ReviewReport` stays the single source of truth; the agent explains it.
- No pipeline/stage/executor/checkpoint/supervisor/loop change; all Phase 25-30
  behaviour and interfaces preserved.

## Alternatives considered

- **Supervisor capability** instead of an agent: rejected - would blur
  orchestration with domain reasoning.
- **Loop evolution** (feed review into `decide()`): rejected - would break the
  loop's determinism and terminal-reason contract.
- **On by default**: rejected - would make runs non-deterministic by default,
  breaking a guarantee held across six phases.

## Limitations / future work

The LLM enrichment refines prose only; richer semantic review (e.g. detecting
missing scenarios the requirements imply) remains future work and would still
consume, never recompute, the deterministic findings.
