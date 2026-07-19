# ADR-010: Prompt templates use string.Template, not str.format

**Status:** Accepted · **Date:** 2026-07-19

## Context

Prompt templates for structured output routinely contain literal JSON
examples. With `str.format`, every `{` and `}` in those examples would need
escaping as `{{`/`}}`, making templates unreadable and making a missed escape
a runtime `KeyError` deep inside a pipeline run. Jinja2 would solve this but
adds a dependency and a full template language V1 does not need.

## Decision

- `PromptLoader.render()` uses `string.Template` with `$variable`
  placeholders. JSON braces pass through untouched.
- Rendering is strict in both directions: a placeholder without a supplied
  value **and** a supplied value without a placeholder each raise
  `ConfigurationError`. A typo in a variable name fails loudly before any
  tokens are spent, instead of silently sending an incomplete prompt.
- Template files are named `<name>_<version>.md` and selected by
  `QAOPS_PROMPT_VERSION` (see ADR-002 on prompt versioning).

## Consequences

- Zero new dependencies; templates stay copy-pasteable JSON-friendly text.
- No conditionals or loops inside templates. Accepted — logic belongs in
  stage code, not in prompt files; a template needing loops is a design
  smell under this architecture.
