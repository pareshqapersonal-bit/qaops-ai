# ADR-013: Second provider (Gemini) via factory selection and optional extra

**Status:** Accepted · **Date:** 2026-07-19 · **Relates to:** ADR-002, ADR-005, ADR-009

## Context

ADR-002 scoped V1 to a single real provider behind the `LLMClient` interface,
with multi-provider support as the interface's promised payoff. Before Phase 4
builds more LLM-backed stages, that promise should be validated: if adding a
provider requires touching anything outside `qaops/llm/`, the boundary is
wrong and cheaper to fix now.

## Decision

1. **`GeminiClient`** joins `AnthropicClient` as a second `LLMClient`
   implementation — the same thin-translation pattern, fully isolated in
   `qaops/llm/gemini_client.py`. Schema retries stay provider-agnostic in
   `generate_structured`.
2. **Selection is configuration-only** via a small factory
   (`create_client(settings)`): `QAOPS_PROVIDER=anthropic|gemini`. This is
   not the plugin registry ADR-005 deferred — it is one function with one
   `if` per provider, promoted exactly when the concrete need (a second
   provider) arrived. `mock` remains test-only and is rejected by the
   factory.
3. **Per-provider model settings:** `model` keeps its existing meaning
   (Anthropic) for backward compatibility; `gemini_model` (default
   `gemini-2.5-flash`, env `QAOPS_GEMINI_MODEL`) is added. The naming
   asymmetry is a deliberate compat cost; unification to `anthropic_model`
   is a candidate for the 1.0 breaking window.
4. **Key handling per ADR-009:** `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) from
   the environment only, checked at construction with a fail-fast
   `ConfigurationError`.
5. **Optional dependency:** `google-genai` ships as the `gemini` extra
   (`pip install "qaops-ai[gemini]"`) so the core install stays lean; the
   `dev` extra includes it so CI lint/type/test coverage is unconditional.

## Consequences

- The abstraction is validated: no domain model, pipeline stage, wire
  schema, prompt, or business-logic change was required.
- Cross-provider prompt-quality parity is NOT asserted: prompts are tuned
  against Anthropic models; Gemini output quality is judged via the
  evaluation script (ADR-008), not assumed.
- Cost: two SDKs to track for breaking changes, and per-provider transport
  behavior (timeout/retry defaults) is not identical. Accepted and
  documented rather than papered over.
