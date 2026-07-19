# ADR-003: Strict typed Pydantic models between all pipeline stages

**Status:** Accepted · **Date:** 2026-07-10

## Context

LLM output is untrusted input. If stages pass raw dicts or raw JSON,
hallucinated fields, missing links, and malformed structures propagate
silently until they corrupt an export or a coverage verdict far from the
source of the error.

## Decision

- Every stage boundary is a Pydantic v2 model defined in `qaops/models/`.
  Raw dicts never cross a stage boundary.
- All models inherit `extra="forbid"`: an LLM response containing a field the
  schema does not define fails validation immediately (and triggers the
  retry loop from ADR-002) instead of being silently dropped or accepted.
- Domain invariants live in the models, not in stage code: ID patterns
  (`REQ-\d{3,}` etc.), non-empty required text, at-least-one requirement link
  per scenario/test case, sequential 1..N step numbering.
- `TestStep`, `TestCase`, and `TestDesignResult` carry `__test__ = False`
  because their names match pytest's collection convention — an occupational
  hazard of building a QA tool in Python.

## Consequences

- Validation failures surface at the exact stage that produced bad data,
  with a field-level error message.
- The models double as the LLM output schema: one source of truth for what
  "valid" means.
- Cost: schema evolution requires a model change plus a prompt change in
  lockstep. Accepted — that coupling is real and should be visible.
