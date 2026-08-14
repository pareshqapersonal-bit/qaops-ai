# ADR-054: NVIDIA registry registration + image-aware provider selection

**Status:** Accepted · **Date:** 2026-08-13 · **Phase 38** · **Relates to:** ADR-052 (visual evidence seam), Phase 37 (NVIDIA provider), ADR-029/030 (adaptive execution)

## Context

Phase 37 added the NVIDIA (Nemotron) provider to the LLM factory
(`create_client`), but image tickets in production still failed with
"all providers failed, last error from gemini". Investigation found two defects in
the execution layer:

1. **Registry gap.** The adaptive executor builds its provider chain from
   `qaops/execution/registry.py`, not the factory. That registry had no `nvidia`
   entry (absent from `_IMPLEMENTED` and `_REGISTRY`), so `get_provider("nvidia")`
   returned None, `QAOPS_PROVIDER=nvidia` was silently ignored, and the chain fell
   back to the text-only providers with keys (gemini/groq/openrouter).
2. **Selection was not image-aware.** `StageRequirements`/`_passes_filter` filtered
   on text/structured/free capability but nothing about images, so even once nvidia
   was registered, a nvidia failure could recover onto a text-only provider, which
   then hard-failed on the image evidence (the analyzer's 36A safety net) - the
   confusing gemini error.

The image transport (36A/36B) and the analyzer hard-fail were both working
correctly; the bug was entirely in provider registration and selection.

## Decision

Register NVIDIA in the execution registry and make provider selection image-aware,
with capability filtering, so image-bearing runs only ever consider image-capable
providers and fail fast with a clear message when none is available.

1. **Model-level capability (source at the provider).** `ProviderInfo.images: bool`
   (registry, defaults False) is the source capability; it populates
   `ModelInfo.images_supported: bool` (defaults False) for the provider's
   candidates. The selector filters on `ModelInfo.images_supported`, consistent with
   the existing `text_capable`/`structured_output`/`free` capability filtering.
2. **NVIDIA registry entry.** Added to `_IMPLEMENTED` and `_REGISTRY` with
   `key_variables=("NVIDIA_API_KEY",)`, `structured_output=True`, `images=True`, and
   `priority=60` - behind the free/preferred providers for normal text failover, so
   existing text ordering is undisturbed. For image runs, capability filtering makes
   it the eligible candidate regardless of that priority.
3. **Run-level image requirement.** `DesignService._execute` derives
   `requires_images = evidence is not None and evidence.has_images` (the Phase 36B
   evidence already in scope) and passes it to `AdaptiveExecutor`, which sets
   `StageRequirements(needs_images=...)`. No per-stage image plumbing; the whole run
   requires an image-capable provider when it carries evidence (downstream stages
   send no images, so an image-capable provider serves them fine).
4. **Fail fast, clearly.** When a run carries images but no eligible provider is
   image-capable, provider selection raises a clear `StageError` before any provider
   call: it states the run includes image evidence, that no configured provider
   supports image input, and points to `QAOPS_PROVIDER=nvidia`. No fallback to
   text-only providers, no silent image drop, no OCR downgrade.

## Consequences

- `QAOPS_PROVIDER=nvidia` is honored and leads the chain; NVIDIA appears in
  `available_providers()` when `NVIDIA_API_KEY` is set.
- Image-bearing runs select NVIDIA and never recover onto gemini/groq/openrouter/
  anthropic (their `images_supported` is False).
- Text-only runs are unaffected: `needs_images=False` makes the new filter clause a
  no-op, so selection and multi-provider fallback are byte-identical to before.
- The analyzer's 36A hard-fail remains as a last-resort safety net but is now rarely
  reached, because selection prevents image runs from ever choosing a text-only
  provider.
- The image capability now lives in two layers (registry `ProviderInfo.images` +
  client `supports_images`); a regression test pins their consistency.

## Scope

Changed: `qaops/execution/registry.py`, `qaops/execution/selector.py`,
`qaops/execution/executor.py`, `qaops/services/design_service.py`. Untouched:
`ImagePart`, `EvidencePackage`, `LLMMessage`, `LLMRequest`, `RequirementAnalyzer`,
`structured.py`, `execute_run`, the evidence sidecar, pipeline stages, and the
PRD/document flow.

## Alternatives considered

- **Provider-level filtering only** (`ProviderInfo.images` alone): rejected - the
  selector already filters on `ModelInfo`, so model-level is the consistent seam
  (the provider flag remains the source that populates it).
- **Per-stage image requirement**: rejected - more plumbing (the executor would need
  to know which stage carries evidence) for no benefit; run-level is simpler and
  correct since only the analyzer sends images and downstream stages tolerate an
  image-capable provider.
- **Silent fallback / auto-switch to a text provider**: rejected - would drop visual
  evidence silently, violating the 36A guarantee.
