# ADR-034: Free-capacity expansion — Groq, free-execution strategy, OpenRouter provider-wide quota

**Status:** Accepted · **Date:** 2026-07-29 · **Relates to:** ADR-026, ADR-027 (adaptive execution), ADR-030 (bounds)

## Context

QAOps's independent free inference capacity was a single provider (OpenRouter
`:free` discovery) plus paid providers. Real runs exhausted OpenRouter's
account-wide daily cap and then had no independent free path. We want more free
capacity and a way to run *only* on free capacity, without changing the existing
AdaptiveExecutor architecture (model→model then provider→provider failover,
provider health memory, run-wide model exclusion, bounded retries/call budgets,
missing-key skipping, OpenRouter free-model discovery — all preserved).

Two design forces shaped this:

1. **Free/paid eligibility is not a provider property.** OpenRouter and Gemini
   each expose both free and paid models (Gemini's 2.5-flash tier is free via an
   API key; 2.5-pro moved behind billing). A `ProviderInfo.free: bool` would be
   wrong.
2. **OpenRouter's free daily cap is account-wide**, shared across all `:free`
   models. Once hit, trying more free models only wastes calls — but a
   model-specific or transient 429 should still get bounded retry.

## Decision

### Add Groq behind the existing LLMClient abstraction

`GroqClient` reuses the OpenAI-compatible client pattern (same `openai` SDK as
OpenRouter, base URL `https://api.groq.com/openai/v1`) — no new dependency. Its
models are registered in the static table with verified IDs
(`llama-3.3-70b-versatile`, `openai/gpt-oss-120b`, `llama-3.1-8b-instant`), all
marked `free=True`. Groq ranks ahead of OpenRouter as a free failover
(independent account quota, strict `json_schema` output). A missing
`GROQ_API_KEY` simply makes Groq unavailable via the existing
`api_key_present()` gate.

### Per-candidate eligibility + an execution strategy

Eligibility lives where it belongs — on `ModelInfo.free`, per candidate — so a
provider can offer both free and paid models. The new `ExecutionStrategy` enum
decides how that flag is used:

- **ANY** (default): unchanged behaviour. Backward-compatible; existing runs are
  unaffected unless a strategy is explicitly selected.
- **FREE_FIRST**: free-eligible providers ordered ahead of paid; paid used only
  after free capacity is exhausted.
- **FREE_ONLY**: only free-eligible candidates. Providers with no free candidate
  (Anthropic) are dropped from the provider set entirely, so **Anthropic is
  never invoked**. Within a mixed provider (Gemini, OpenRouter), only the free
  models pass the filter.

The strategy turns into a `StageRequirements.free_only` filter in the selector
and a provider filter/ordering in the executor. Synthetic single-model
candidates (providers with no catalogue) are stamped with the correct `free`
flag from the configured model, so Gemini flash is treated as free and Anthropic
as paid.

### OpenRouter provider-wide quota recognition

A new `FailureKind.PROVIDER_RATE_LIMIT` matches account-wide exhaustion wording
(`free-models-per-day`, `free model requests per day`, `requests per day`,
`daily limit`, `quota exceeded for`), matched *before* the generic rate-limit
patterns. It maps to `DISABLE_AND_SWITCH`, so an account-wide OpenRouter 429
disables the provider for the rest of the run instead of walking to more free
models. Model-specific/transient 429s keep matching the generic `RATE_LIMIT`
pattern (`rate limited`, `too many requests`, bare `429`) and retain bounded
`RETRY_SAME_WITH_BACKOFF` — the provider stays usable.

### Bounds and telemetry unchanged

`max_models_per_provider_per_stage=5`, `max_stage_recovery_attempts=12`, and
`max_provider_calls_per_stage=20` are unchanged. Telemetry needs no new system:
existing `ExecutionEvent` fields (`provider`, `model`, `provider_call_number`,
`recovery_attempts`, `failure_kind`) and events (`MODEL_SWITCH`,
`PROVIDER_SWITCH`, `PROVIDER_EXHAUSTED`), plus `ProviderHealth.reason` (the
disable reason), already expose provider/model selected, call counts, recovery
actions, failure reason, switches, and disable reason.

## Consequences

- One independent free provider (Groq) is added with its own account quota,
  materially improving free-only resilience: a run that dies when OpenRouter's
  daily cap is hit now continues on Groq.
- `FREE_ONLY` guarantees no paid spend, and provably never touches Anthropic.
- Default (`ANY`) runs are byte-for-byte unchanged; the whole feature is opt-in.
- Model IDs and free-tier facts are point-in-time (verified July 2026);
  providers rotate models, so the static table may need periodic refresh.

## Alternatives considered

- **`ProviderInfo.free: bool`** — rejected; cannot represent providers that
  offer both free and paid models.
- **Adding Cerebras/Mistral/Hugging Face now** — deferred: Cerebras now requires
  a payment method, Mistral needs phone verification and publishes no rate
  numbers, HF's free allowance is ~$0.10/month. Groq alone meets every hard
  requirement.
- **Raising recovery bounds** — rejected; OpenRouter's account-wide cap means
  more per-provider model attempts don't buy capacity. The account-wide-429
  reclassification is the correct lever.
