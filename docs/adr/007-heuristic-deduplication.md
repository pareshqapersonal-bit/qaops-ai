# ADR-007: Heuristic deduplication that flags, never deletes

**Status:** Accepted · **Date:** 2026-07-10

## Context

"Avoid duplicate scenarios" needs a definition. True semantic deduplication
requires embeddings: a new dependency, per-run cost, and another source of
non-determinism. Worse, silent deletion of a false-positive "duplicate" is a
coverage loss the user never sees — the exact failure mode ADR-001 exists to
prevent.

## Decision

- V1 deduplication is pure-code heuristics: normalize titles, then flag pairs
  sharing the same requirement ID + same test type + high title token
  overlap.
- Suspected duplicates are *reported* in `CoverageReport.suspected_duplicates`
  as ID pairs. Nothing is auto-deleted; removal is the human's call.
- Embedding-based semantic dedup is a V2 candidate behind the same interface.

## Consequences

- Zero new dependencies, deterministic, fully unit-testable.
- Some near-duplicates with dissimilar titles will slip through. Accepted —
  a false negative costs review time; a false-positive deletion costs
  coverage.
