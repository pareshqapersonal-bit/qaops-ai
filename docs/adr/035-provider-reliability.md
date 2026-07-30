# ADR-035: Provider reliability — model eligibility, availability, failure scope & observability

**Status:** Accepted · **Date:** 2026-07-30 · **Relates to:** ADR-026/027 (adaptive execution), ADR-030 (bounds), ADR-034 (free expansion)

## Context — production incident

A production smoke test (a small BOGO promotion PRD) failed in the first
pipeline stage, `requirement_analyzer`. Provider failover itself worked
(OpenRouter → Groq → Gemini); the failure was that every candidate was
unusable. The logs showed four distinct defects:

1. OpenRouter discovery selected `google/lyria-3-clip-preview` and
   `google/lyria-3-pro-preview` — music-generation models — for a text stage.
2. Groq models each surfaced `rate_limit` then `unknown`.
3. Gemini's configured `gemini-2.5-flash` returned `404 … no longer available
   to new users`.
4. The UI showed only "Last error from gemini/gemini-2.5-flash", hiding that
   OpenRouter and Groq had already been exhausted.

## Decisions

### Model eligibility is capability-first, not name-based

`ModelInfo` gains `text_capable`. OpenRouter discovery reads
`architecture.input_modalities`/`output_modalities` and sets `text_capable`
False for models that do not take text in and emit text out (a music/image
generator). The selector filters non-text models **before** ranking, so a large
context window can never make an unsuitable model competitive. Metadata absent →
default True (conservative; never silently exclude usable models). A small
capability-oriented name guard is used only for Gemini multimodal generators
that still advertise `generateContent`. Cost and suitability are independent
dimensions: a model may be free+unsuitable, free+suitable, paid+unsuitable, or
paid+suitable, and each is reasoned about separately.

### Gemini availability via discovery, static as resilient fallback

Gemini gains real discovery (`discover_gemini_models`) through the google-genai
SDK's `client.models.list()`, keeping only models whose `supported_actions`
include `generateContent`, normalized into `ModelInfo` and cached by the
existing `ModelRegistry`. Discovery degrades to a curated static table on any
failure. The static table now uses Google's **stable `*-latest` aliases**
(`gemini-flash-latest`, `gemini-flash-lite-latest`, `gemini-pro-latest`) rather
than a pinned generation, so the fallback is not itself a stale single point of
failure the way `gemini-2.5-flash` became. A model returning MODEL_UNAVAILABLE
is dropped for the remainder of the run (no pointless retries).

### Failure classification uses sanitized structured fields

`classify_failure` matched message substrings only; a 429 whose body text lacked
a known substring fell through to UNKNOWN — the `rate_limit → unknown`
sequence. `LLMProviderError` now optionally carries sanitized `status_code` and
`error_code`, populated at the provider boundary via
`extract_openai_error_fields`. `classify_failure_fields`/
`recovery_for_exception` resolve in order: provider-wide error code
(`insufficient_quota`, billing hard limit) → PROVIDER_RATE_LIMIT; then the
existing message-text patterns; then HTTP status (429→rate limit, 402→credit,
404→unavailable, 401/403→auth, 5xx→transient). A plain 429 is never treated as
provider-wide exhaustion — scope is decided by error code, not status.

### Failure-scope → recovery matrix

| Kind | Scope | Action |
|---|---|---|
| MODEL_UNAVAILABLE | model | drop model, continue |
| INSUFFICIENT_CREDIT | model | next model |
| PROVIDER_RATE_LIMIT | provider/account | disable provider for run |
| RATE_LIMIT | transient/model | bounded retry with backoff |
| TIMEOUT | transient | bounded retry |
| EMPTY_OUTPUT / INVALID_OUTPUT | model | next model (no infinite repair) |
| AUTHENTICATION | provider | disable provider for run |
| UNKNOWN | unknown | bounded safe recovery (next model) |

### Attempt-history observability

`ExecutionReport` gains an ordered `attempts` list of sanitized `AttemptRecord`
(stage, provider, model, failure_kind, optional status_code/error_code). On a
terminal stage failure the executor attaches this to `StageError.attempts`; the
API surfaces it as `attempt_history` on the run status response. The frontend
can show the full failover story instead of only the last error. No second
analytics system — this reuses the existing report/event structures.

### Security / sanitization

Only normalized fields are ever recorded: provider, model, failure kind, HTTP
status, and a length-capped provider error code. Never keys, Authorization
headers, request payloads, or raw response bodies. A regression test plants
credential-shaped strings in an error and asserts they never appear in the
attempt history.

### Bounded execution preserved

`max_models_per_provider_per_stage=5`, `max_stage_recovery_attempts=12`, and
`max_provider_calls_per_stage=20` are unchanged. Discovery may return hundreds
of models; candidate execution stays bounded. ANY/FREE_FIRST/FREE_ONLY semantics
are unchanged, and FREE_ONLY still never invokes a paid candidate — during
initial selection or recovery.

## Consequences

- Non-text models cannot be selected for pipeline stages.
- Gemini survives model retirement via discovery + stable-alias fallback.
- The `rate_limit → unknown` ambiguity is resolved by structured fields.
- Failed runs expose an actionable, sanitized attempt history.
- Model IDs remain point-in-time; discovery is the primary source, so a stale
  static entry no longer strands a provider.
