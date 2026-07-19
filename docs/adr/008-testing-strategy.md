# ADR-008: Testing strategy for non-deterministic components

**Status:** Accepted · **Date:** 2026-07-10

## Context

"Strong pytest coverage" conflicts with non-deterministic LLM output: you
cannot assert that a model produces a specific test case. A naive test suite
would either mock nothing (flaky, slow, needs secrets in CI) or assert
nothing meaningful.

## Decision

Split the test strategy along the ADR-001 line:

1. **Deterministic code** (models, IDs, pipeline runner, validator, dedup,
   exporters, config): conventional unit tests, the highest coverage in the
   repo, run in every CI job.
2. **LLM-backed stages**: unit-tested against `MockLLMClient` with canned
   responses — asserting schema validation, retry behavior, and error
   handling, not creative content.
3. **Live evals**: an optional suite marked `@pytest.mark.llm`, excluded
   from CI (`pytest -m "not llm"`), run locally against real fixture BRDs to
   judge output quality. The marker is registered in `pyproject.toml` from
   Phase 0 so the split is enforced before any LLM code exists.

## Consequences

- CI is fast, deterministic, and needs no API key.
- Output *quality* is measured by human review of live evals, not by CI —
  an honest reflection of what automated tests can and cannot prove here.
