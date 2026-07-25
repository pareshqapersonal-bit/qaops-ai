# ADR-026: Provider failover by rebuilding remaining stages, not by mutating them

**Status:** Accepted · **Date:** 2026-07-24 · **Relates to:** ADR-002, ADR-022, ADR-023, ADR-025

## Context

Execution was all-or-nothing: one provider failure — an exhausted OpenRouter
balance, a rate limit — ended the run, discarding every completed stage. The
user restarted from the beginning with a different provider, paying again for
work already done.

## Decision

Add an executor above the pipeline that classifies failures, applies a
per-failure policy, and switches providers when appropriate. No pipeline stage,
prompt, exporter, or `PipelineBuilder` changes.

1. **Switching works by rebuilding, not mutating.** Stages take their client in
   `__init__` and hold it, so a constructed stage cannot be handed a new
   provider — and changing that would mean modifying every stage, which the
   phase forbids. Instead, on a switch the executor calls the stage factory
   again with settings naming the new provider, and resumes at the failed
   index. Constructing stages is cheap, completed outputs are already
   checkpointed, and every stage remains completely unaware that a provider
   changed. This is the same boundary ADR-002 already draws.

2. **Failures are classified, and policy differs by kind.** Exhausted credit
   and rejected credentials disable the provider outright — retrying is
   guaranteed to fail identically. Rate limits retry the same provider with
   backoff. Timeouts and schema-validation failures retry the same provider,
   since both are frequently transient. Context-limit overflow switches without
   disabling, because a different model may have headroom. Treating every error
   the same either wastes attempts on the hopeless or abandons the recoverable.

3. **A provider registry, not a hardcoded list.** One table describes each
   provider: its key variables, whether it runs locally, its relative priority,
   and whether it produces structured output reliably. Adding Ollama or Azure
   OpenAI means adding a row and a client. Preflight (ADR-025) now reads its
   key-variable metadata from this registry rather than keeping a second copy.

4. **Configuration wins; discovery is the default.** The configured provider
   leads the chain, and every other provider with credentials present follows
   in priority order. Failover therefore works with no extra setup, while an
   explicit choice is still honoured.

5. **Availability requires an implementation.** Ollama needs no API key, so a
   credentials-only check would report it available on every machine and the
   executor would switch to a provider that cannot work. Automatic discovery
   is restricted to providers with a client.

6. **Health is per-run state, held by the executor, not the registry.** The
   registry describes static facts; a provider exhausted today should be tried
   again tomorrow.

7. **Checkpoints are in memory, for the duration of one run.** That covers the
   failure this phase exists to fix — a provider dying mid-pipeline. Persisting
   them across process restarts raises staleness questions (is yesterday's
   checkpoint still valid for a document that has since changed?) and is
   deliberately out of scope.

## Consequences

- A run survives a provider failing halfway, and completed stages are never
  recomputed. The user sees which provider ran each stage and why any switch
  happened.
- Cost: a switch rebuilds the remaining stages, so a stage that failed after
  partial work restarts that stage from the beginning. Stage-internal progress
  is not checkpointed, only stage boundaries.
- The registry now carries capability data (local, structured output, priority)
  beyond what failover strictly needs, so later decisions — prefer a local
  model for a large document to preserve cloud credit — have the information
  without another lookup table.
- This is adaptive *execution*, not an agent: the executor follows a fixed
  policy table, does not plan, and does not choose tools. It recovers; it does
  not decide what to do next.
