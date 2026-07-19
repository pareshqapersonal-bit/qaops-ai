# ADR-012: Generation-time duplicates fail loudly; near-duplicates flag later

**Status:** Accepted · **Date:** 2026-07-19

## Context

"Do not generate duplicate scenarios" needs an enforcement point. ADR-007
already decided that the Phase 5 Deduplicator *flags* suspected
near-duplicates and never deletes. But that leaves open what happens when a
single generation response contains outright duplicates — the model
violating an explicit prompt instruction within one output.

Silently dropping generation-time duplicates would hide a prompt-quality
problem; keeping them would corrupt every downstream count and coverage
verdict.

## Decision

Two distinct mechanisms at two distinct points:

1. **Generation time (`ScenarioGenerator`):** exact duplicates — same
   category plus casefolded, whitespace-normalized title — raise `StageError`
   naming the duplicated titles. This is deterministic code, cheap, and
   treats an instruction-violating response as untrustworthy rather than
   repairable-by-deletion.
2. **Validation time (Phase 5 `Deduplicator`):** heuristic *near*-duplicate
   detection (shared requirement + type + high title token overlap) reports
   suspected pairs in `CoverageReport.suspected_duplicates` for human review,
   per ADR-007. Nothing is deleted at either point.

## Consequences

- A duplicate-producing prompt or model fails visibly in one run, pointing
  at the prompt as the thing to fix — instead of silently degrading output.
- The retry loop does not attempt to "repair" duplicates: schema-valid but
  instruction-violating output is a quality failure, not a format failure,
  and re-rolling the dice would mask it.
- Cost: a rare legitimate near-collision in titles fails the run. Accepted —
  titles are required to be specific and unique; a collision means they
  weren't.
