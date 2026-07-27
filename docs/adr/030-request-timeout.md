# ADR-030: A per-request deadline with QAOps-owned retries, and unambiguous progress

**Status:** Accepted · **Date:** 2026-07-26 · **Relates to:** ADR-026, ADR-029

## Context

After the Phase 16.1 count bounds landed, a real PDF run still stalled: the API
sat at `status: running` for ~12 minutes with no new events. The recovery
*count* was bounded (four models had failed, a fifth was in flight), but a
single request to a hanging free-tier model held the stage indefinitely.

Inspection of the provider clients found the real cause. Every client already
set `timeout_seconds = 120.0`, but two things defeated it:

1. The timeout was **hardcoded and never wired to settings** — the factory
   constructed clients as `AnthropicClient(model=...)`, dropping the timeout, so
   it could not be configured per run.
2. The **OpenAI SDK (used for OpenRouter) defaults to `max_retries=2`**, and the
   OpenRouter client never overrode it. One QAOps "attempt" therefore became up
   to three SDK requests, each up to 120s — roughly six minutes of silent
   internal retrying before QAOps saw a single failure. Anthropic's SDK retried
   similarly.

So the stall was not a missing timeout. It was an unconfigurable timeout
multiplied by hidden SDK retries.

## Decision

Bound request *duration*, and make QAOps the sole retry owner so the bound
means what it says. Reuse the existing Phase 15/16.1 recovery hierarchy — no new
retry engine.

1. **`request_timeout_seconds`, default 60.** A validated setting
   (`gt=0, le=1800`), backward-compatible with configs that omit it. The factory
   passes it to every client, which applies it at the SDK/HTTP boundary
   (Anthropic and OpenAI in seconds, google-genai in milliseconds). It bounds a
   *single generation request*, not a stage, the pipeline, or all retries
   combined. 60s rather than the prior 120s because the failure mode is a
   hanging free-tier model; 120s would let each such model hold a stage for two
   minutes before recovery.

2. **QAOps owns retries; SDK retries are disabled.** `max_retries=0` on the
   OpenAI (OpenRouter) and Anthropic SDKs. One QAOps attempt is now exactly one
   network request with one deadline, so the four count bounds compose cleanly
   with the duration bound instead of multiplying against hidden SDK attempts.
   The adaptive executor is the single retry authority.

3. **Timeouts normalize at the provider boundary.** A new `llm/timeouts.py`
   detects SDK timeout exceptions (by class-name across the MRO, plus
   unambiguous text markers) and rewrites the message so the existing policy
   classifies it as `FailureKind.TIMEOUT`. It is conservative: a plain
   connection error is *not* a timeout and passes through unchanged to classify
   on its own terms. The executor never sees provider-specific timeout types.

4. **Timeout recovery is the existing hierarchy.** A timeout retries the same
   model (bounded by `max_attempts_per_model`), then moves to the next ranked
   model (bounded by `max_models_per_provider_per_stage` = 5), then the next
   provider (bounded by `max_stage_recovery_attempts` = 12). No counter resets
   incorrectly; timeout retries cannot bypass the 16.1 limits.

   The four bounds interact as: **duration** caps one request; **attempts** caps
   same-model retries of that request; **models-per-provider** caps distinct
   models on a provider; **stage-recovery** caps total switches. Same-model
   retries do not consume the recovery budget — they are bounded independently.

5. **Request lifecycle events and unambiguous counters.** The executor emits
   `request_started` before each call (making an in-flight request visible),
   `request_timed_out`, and `request_retry`, alongside the existing
   model/provider events. Two new counters replace the ambiguous
   `models_attempted` reported by 16.1: `model_attempt_number` (which distinct
   model this is for the stage) and `request_attempt` (which network request for
   the current model, resetting on a model switch). `models_attempted` is
   retained for compatibility. The API surfaces all of these; a UI no longer has
   to infer request state from logs.

6. **All execution routes through the executor.** Phase 16.1 ran single-provider
   pipelines directly, so those runs had no progress. Routing every run through
   `AdaptiveExecutor` — which handles a one-provider list fine — closes that gap
   with no change to pipeline semantics. A provider with no model metadata (the
   mock provider, or a provider whose discovery yields nothing) gets a single
   synthetic candidate so it stays executable.

## Consequences

- No single request can hold a stage indefinitely; the hanging-model stall is
  bounded to `request_timeout_seconds` per attempt, with same-model retries
  capped. The live regression is locked in by a test.
- Disabling SDK retries means QAOps no longer benefits from the SDKs' transient-
  error backoff — but it now owns that behavior explicitly through the policy
  table, which is the point: one predictable retry authority.
- **Known limitation — cancellation.** The deadline is enforced by the SDK/HTTP
  client, which returns control on timeout; QAOps does not itself kill an
  in-flight socket. If a provider SDK failed to honor its own timeout, QAOps
  could not force cancellation. We rely on the client honoring the deadline
  (all three do); a threading watchdog was rejected because it would leak the
  request rather than cancel it (section 12).
- Progress is now accurate and observable for both single- and multi-provider
  runs, without the API parsing any log text.
- Still deterministic and non-agentic: a timeout is one more classified failure
  feeding a fixed policy table.

---

## Addendum: nested structured-output retries and true call budgeting

**Date:** 2026-07-26 (acceptance fix)

The first cut of this ADR bounded request *duration* and disabled SDK retries,
but the real PDF acceptance run exposed a second multiplier one layer down.

### What the acceptance test found

`generate_structured` (the parse → validate → repair loop, ADR-002) runs *below*
the executor: the executor counts one `stage.run()` as one attempt, but that
single call could make up to three real `client.complete` calls (a repair loop
of `llm_retries + 1`). So one executor "request attempt" hid three provider
calls, and when the executor then retried the same model on `invalid_output`, it
started another three. Progress showed `request_attempt: 2` while the terminal
showed six real calls. The count bounds (5 models, 12 recoveries) were intact,
but they bounded the wrong unit.

### Retry ownership after the fix

The executor owns *when to stop*; the structured layer owns *local deterministic
work* (JSON extraction, schema validation, one bounded repair loop). Every real
`client.complete` is now announced to a `RequestObserver` that the executor
binds around each `stage.run()` via a context variable. The observer counts the
call, emits a `REQUEST_STARTED` / `REQUEST_COMPLETED` pair, and can raise
`RequestBudgetExhausted` to stop further calls. This is the smallest change that
makes the hidden calls visible without threading an observer through every stage
constructor or building a second retry engine.

### The 3-attempt loop: kept, but budgeted

The three attempts were a genuine schema-*repair* prompt (`with_feedback` appends
the failed response and the validation error so the model can self-correct), not
a blind resend. That is worth keeping for a substantial-but-malformed response,
so it stays — but every attempt is now an observed, counted, vetoable provider
call. For an *empty* response the loop stops immediately: re-prompting a model
that returned nothing cannot help.

### Counter and recovery semantics

- **request_attempt**: the attempt index within one structured-output
  invocation (the repair loop), 1-based.
- **provider_call_number**: the running total of *actual* provider calls for the
  stage, across all models and repair attempts. This is the honest number to
  trust; it is what progress and the budget use.
- **model_attempt_number**: which distinct model this is for the stage.
- **recovery_attempt**: a model switch or provider switch (unchanged from
  ADR-029); same-model repair calls are *not* recoveries.

### Bounds on total provider calls

A new `max_provider_calls_per_stage` (default 20) caps *actual* provider calls
per stage, enforced by the observer. It is a distinct budget from
`max_stage_recovery_attempts` (which counts switches), deliberately not a
redefinition, so the documented recovery semantics are preserved. Worst case is
`max_models_per_provider_per_stage (5) × (llm_retries + 1) (3) = 15`, under the
20 ceiling; the budget is the backstop that guarantees no multiplication beyond
it. SDK retries remain disabled, so there is no third multiplier.

### invalid_output and empty_output

`invalid_output` now recovers with `NEXT_MODEL`, not `RETRY_SAME`: a model that
reaches the executor with invalid output has already spent its in-request repair
attempts, so another full nested cycle on the same model is waste. A brand-new
failure kind, `empty_output`, covers a zero-content response; it also goes to
the next model and never triggers a repair re-roll.

### Truncation diagnostic corrected

`stop_reason=length` with `content=""` (`chars=0`) is no longer diagnosed as
token truncation. Empty content is recognized first and raised as
`LLMEmptyResponseError`, which never recommends raising `max_output_tokens`. The
"raise the token cap" advice is emitted only when a response actually had
content that was cut off. `LLMResponseFormatError` additionally refuses to give
token-cap advice when every raw response was empty, as a defense in depth.
