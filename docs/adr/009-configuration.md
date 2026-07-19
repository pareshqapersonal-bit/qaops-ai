# ADR-009: Configuration via pydantic-settings, constructor injection

**Status:** Accepted · **Date:** 2026-07-10

## Context

Configuration must be overridable per environment (local dev, CI, demo)
without code changes, and secrets must never live in config files or the
repo.

## Decision

- One `QAOpsSettings` class (pydantic-settings). Every field has a sane
  default and a `QAOPS_*` environment override; values are validated at load
  (provider whitelist, temperature range, known export formats).
- The Anthropic API key is read from the standard `ANTHROPIC_API_KEY`
  environment variable only. It has no field in config files and is never
  logged.
- Components receive settings by constructor injection. There is no global
  settings singleton — tests construct settings explicitly, and two pipeline
  instances with different configs can coexist.

## Consequences

- Reproducible runs: the effective config is explicit at construction.
- Slightly more wiring in composition code. Accepted — the wiring lives in
  one place (the agent/CLI composition root).
