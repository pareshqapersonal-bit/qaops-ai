# ADR-002: Single LLM boundary behind an `LLMClient` interface

**Status:** Accepted · **Date:** 2026-07-10

## Context

The LLM boundary is the highest-risk component: provider outages, malformed
output, token limits, and cost all live there. Scattering `anthropic` SDK
calls across pipeline stages would couple every stage to one vendor and make
unit testing impossible without network access.

Frameworks like LangChain were considered and rejected: they violate the
project's minimal-dependencies and single-responsibility principles, and V1
needs only ~100 lines of client code.

## Decision

- All model calls go through one `LLMClient` protocol (Phase 1) defined in
  `qaops/llm/`.
- **V1 ships exactly one real implementation (`AnthropicClient`) plus
  `MockLLMClient`.** The interface exists from day one so OpenAI or local
  models can be added later without architectural change; implementing them
  now would be speculative.
- A shared structured-output helper owns the parse → Pydantic-validate →
  retry loop (`llm_retries` from settings, default 2). After final failure it
  raises `LLMError` and persists the raw response for debugging — never a
  silent fallback.
- Prompt templates are versioned files in `qaops/prompts/`
  (`analyzer_v1.md`, ...), selected by `QAOPS_PROMPT_VERSION`. Prompt changes
  are diffs in git, not invisible string edits.

## Consequences

- Every pipeline stage is unit-testable against `MockLLMClient`; CI needs no
  API key.
- Provider switch = one new class implementing the protocol.
- Cost: one extra indirection layer. Accepted — it is the load-bearing wall
  of the whole platform.
