# ADR-027: Exhaust models within a provider before switching, and discover them at runtime

**Status:** Accepted · **Date:** 2026-07-25 · **Relates to:** ADR-026

## Context

ADR-026 treated each provider as a single endpoint: one failure and the whole
provider was abandoned. But a provider exposes many models, and the failures
that matter most in practice are model-specific. When one OpenRouter model
exhausts its credit, other models on the same key are still usable — switching
providers there wastes a working credential.

The models a provider offers also change over time, so a hardcoded model list
goes stale. Discovery should come from the provider's own API, not a fixed
table and not an LLM.

## Decision

Make failover two-level — models first, then providers — and discover models at
runtime. No pipeline stage, prompt, exporter, or `PipelineBuilder` changes; the
executor's rebuild mechanism (ADR-026) already switches targets by
reconstructing the remaining stages, and it now varies the model as well as the
provider.

1. **Model failover before provider failover.** On a model-specific failure the
   executor tries the next compatible model on the *same* provider. Only when
   every compatible model there has failed is the provider marked exhausted and
   execution moves on. This is requirement 5 of the phase: credit exhaustion
   and model-unavailable are model problems, not provider problems.

2. **Policy is model-aware.** Insufficient credit → try the next model.
   Model unavailable → drop that model and continue. Context overflow → try a
   model with a larger context window. Authentication failure → disable the
   provider outright, since every model on it shares the rejected credential.
   Rate limit → back off and retry the same model. Timeout and invalid output →
   retry the same model, both being frequently transient.

3. **A ModelRegistry discovers, caches, and describes.** Each provider with a
   discovery endpoint exposes its models through the registry:
   `discover_openrouter_models` parses OpenRouter's public `/models` endpoint;
   `discover_ollama_models` reads a local daemon's tags. Every discovery path
   degrades to a curated static table on any failure — unreachable endpoint,
   timeout, or an unexpected response shape — because discovery sits in the
   execution path and a network problem must cost fidelity, never the run.
   Results are cached per registry instance and refreshed only on explicit
   request; a CLI run lasts a minute, so no background scheduler.

4. **Capabilities, not just names.** Each `ModelInfo` carries context and output
   limits, structured-output support, and locality, so the executor filters
   candidates by what a stage needs (structured output, enough context) rather
   than trusting a name.

5. **A `qaops models` command** lists what each available provider discovers, so
   discovery is observable and verifiable on its own, without waiting for a
   stage to fail.

## Consequences

- A working provider is no longer discarded over one model's credit. The user
  sees exactly which model served each stage and why any switch happened.
- **A real bug this surfaced:** model-first recovery could loop. With a single
  provider and a persistent retryable failure (e.g. timeout), same-model
  retries would exhaust, a sibling model would be tried, attempts would reset,
  and the cycle would repeat forever. The fix tracks models already tried for
  the current stage and excludes them when choosing a sibling, so the candidate
  pool strictly shrinks and exhaustion raises `StageError` instead of hanging. A
  regression test (`test_single_provider_persistent_retryable_terminates`) locks
  this in.
- **Discovery is unverified against live APIs.** No network access in the build
  environment means the OpenRouter and Ollama clients are tested only against
  mocked responses shaped from documentation. If a real response differs,
  discovery degrades to the static table and execution still works — the
  "runtime" part simply doesn't engage. The `qaops models` command exists so
  this can be confirmed against a real key.
- This remains adaptive *execution*, not an agent: the executor follows a fixed
  policy table and a deterministic candidate order. Discovery is an HTTP GET
  and a parse. Nothing here plans or chooses tools.
