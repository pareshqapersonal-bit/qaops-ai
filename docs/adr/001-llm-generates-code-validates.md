# ADR-001: LLM generates, code validates

**Status:** Accepted · **Date:** 2026-07-10

## Context

The original spec asked the platform to both generate test cases with AI and
validate coverage with AI. If the same model extracts requirements, generates
tests, and then judges whether coverage is complete, it grades its own
homework: a hallucinated "all requirements covered" verdict is undetectable.
Coverage claims are the platform's core credibility feature — a QA tool whose
coverage report cannot be trusted is worse than no tool.

## Decision

Split every responsibility along a hard line:

- **LLM-backed (generation):** requirement analysis, gap analysis, business
  rule extraction, scenario generation, test case generation.
- **Pure Python (validation):** ID assignment, traceability matrix
  construction, coverage math, duplicate detection, export.

Concretely:

1. IDs (`REQ-*`, `BR-*`, `SC-*`, `TC-*`) are assigned by `core.ids.IdGenerator`
   after each generation stage. The LLM never invents IDs; it references IDs
   it was given.
2. Generation stages must emit structured output that links back to
   requirement IDs (enforced by Pydantic validators: a `Scenario` or
   `TestCase` without at least one valid `REQ-*` reference fails validation).
3. `CoverageValidator` and `Deduplicator` make zero LLM calls. They compute
   verdicts from the traceability graph alone.

## Consequences

- Coverage reports are trustworthy and fully unit-testable with plain fixtures.
- The validator can catch generation failures (LLM referencing a nonexistent
  requirement) instead of papering over them.
- Cost: the LLM cannot "reason" about coverage gaps qualitatively; the
  validator only sees structural gaps (missing categories, unlinked
  requirements). Accepted — structural gaps are what a traceability matrix is
  for, and qualitative review remains the human QA engineer's job.
