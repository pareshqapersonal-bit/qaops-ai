# ADR-021: Chunk sizing is adaptive, decided by strategy not configuration

**Status:** Accepted · **Date:** 2026-07-23 · **Relates to:** ADR-002, ADR-020

## Context

ADR-020 made chunking transparent to downstream stages but left sizing to a
fixed `chunk_size`. That forces users to reason about something they should
not have to: the right value depends on the provider, the model, and the
document, and a wrong value fails in opposite directions. Real evidence from
one session: a 7656-character PRD succeeded at `chunk_size: 8000` (no split)
and failed at `chunk_size: 6000` (split into two). The user had to discover
that by trial.

## Decision

Split the *decision* from the *mechanism*.

1. **`ChunkStrategy` decides**: whether to chunk, at what size, with what
   overlap. **`ChunkPlanner` only splits** - it no longer owns any sizing
   policy, keeping it a pure, deterministic text utility.
2. **Sizing is derived from output capacity, not input context.** Every real
   failure observed was `stop_reason=length` during *generation*: the model
   read the document fine but could not emit all extracted requirements in one
   response. Input windows are far larger than the documents QAOps handles, so
   sizing against them would produce chunks that reliably truncate.
   `OUTPUT_EXPANSION_FACTOR` (3.0) estimates how much input text can safely
   produce output within the model's ceiling; it is grounded in a measured run
   where 7.6k characters of PRD produced ~36k characters of requirement JSON.
3. **Capability metadata lives in one registry**, keyed by provider and model,
   with model overrides taking precedence over provider defaults and a
   conservative global fallback. Adding a provider means adding a row - the
   `LLMClient` protocol stays a pure completion boundary (ADR-002) and gains no
   capacity methods.
4. **Automatic bypass.** A document within estimated capacity skips chunking
   entirely and the analyzer runs exactly as it would with no chunking
   involved - not a one-chunk split, an actual bypass.
5. **Adaptive is the default; fixed is an advanced override.**
   `chunking_strategy: fixed` uses `chunk_size`/`chunk_overlap` verbatim.
   Under the adaptive default those two settings are ignored.

Unknown providers and models resolve to the conservative default rather than
raising, so an unrecognised model produces smaller chunks instead of an error.

## Consequences

- Users stop tuning `chunk_size`. Validated against the real case: for
  `openrouter/deepseek-chat` the strategy computes ~8.7k capacity and declines
  to chunk the 7656-character PRD - the configuration that actually worked.
- Weak models are handled honestly: `openai/gpt-oss-20b:free` resolves to
  ~2.2k capacity and chunks even small documents, reflecting its observed
  tendency to truncate.
- Cost: the capability table is an estimate that will drift as providers
  change limits, and `CHARS_PER_TOKEN` and `OUTPUT_EXPANSION_FACTOR` are
  heuristics, not measurements per document. They are deliberately
  conservative, and wrong values degrade to "more chunks than necessary"
  rather than truncation.
- **Not addressed here:** `merge_requirements` still deduplicates on exact
  normalized title (ADR-020). Adaptive sizing avoids chunking small documents,
  which sidesteps the duplicate-requirement problem for them, but a genuinely
  large document that must be chunked can still produce near-duplicate
  requirements with differing titles, which in turn generate duplicate
  scenarios that `ScenarioGenerator` rejects (ADR-012). Chunking therefore
  remains functionally unproven on documents that actually require it. A
  smarter merge is the next necessary step.
