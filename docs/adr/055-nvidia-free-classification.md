# ADR-055: Classify NVIDIA/Nemotron as free so image runs survive FREE_ONLY

**Status:** Accepted · **Date:** 2026-08-14 · **Phase 39** · **Relates to:** ADR-034 (free-tier strategy), ADR-054 (image-aware selection), Phase 37 (NVIDIA provider)

## Context

With `QAOPS_EXECUTION_STRATEGY=free_only`, image tickets failed at provider
selection ("This run includes image evidence, but no configured provider supports
image input"), while PRD/document runs worked. Root cause: NVIDIA is the only
image-capable provider, but `_configured_model_is_free("nvidia")` fell through to
`False` (nvidia is neither gemini nor local), so the FREE_ONLY strategy filter
removed NVIDIA *before* image-capability selection ran. No image-capable candidate
remained, so the run failed fast.

`free` in this codebase is a **cost-based** classification (`ModelInfo.free`): a
model is free when it has zero per-call cost through a free/free-tier endpoint. Every
existing free provider (gemini flash, all groq models) is rate-limited and still
`free=True`, so rate limits do not disqualify "free". NVIDIA's `free=False` was an
unreviewed fallthrough default, not a deliberate "NVIDIA is paid" decision.

## External verification

NVIDIA's hosted catalog at `https://integrate.api.nvidia.com/v1` (build.nvidia.com)
serves Nemotron models free through the NVIDIA Developer Program: no per-token price,
no credit card, OpenAI-compatible.

**CAVEAT (must not be removed):** the free hosted tier is RATE-LIMITED (community
baseline ~40 RPM; free credits can exhaust, after which requests get HTTP 429), and
NVIDIA's FAQ restricts the free tier to development/evaluation - production traffic
(serving real end-users) requires the paid NVIDIA AI Enterprise path. So "free" here
means zero monetary cost per call, consistent with this codebase's cost-based
definition - NOT unlimited throughput and NOT a production SLA.

## Decision

Option A: classify NVIDIA's configured Nemotron model as free. The smallest change is
a `provider == "nvidia"` branch in `_configured_model_is_free` returning `True`,
mirroring the gemini branch, with the rate-limit / production-restriction caveat
documented inline. NVIDIA therefore survives FREE_ONLY filtering and remains eligible
for image runs via image-capability selection.

Explicitly NOT done (rejected alternatives from the review): no image-specific
exception to FREE_ONLY (would let a "paid" provider run under a free-only strategy -
semantic violation); no reordering of strategy-vs-capability filtering (larger blast
radius); no change to Render config or `QAOPS_EXECUTION_STRATEGY`.

## Consequences

- Image tickets work under `free_only` without any configuration change: NVIDIA is
  selected via image-capability filtering.
- **PRD/text ordering is unchanged.** NVIDIA keeps `priority=60`, behind the existing
  free providers (groq=10, gemini), so it is never preferred for text runs and only
  appears as a last-resort free failover. The order among existing providers is
  identical with or without NVIDIA present.
- Image transport is untouched: base64 image data still reaches the client
  byte-identical under `free_only`.
- Operational caveat (above) applies: `free_only` image runs now depend on NVIDIA's
  rate-limited, dev-tier endpoint. This is cost-correct but carries a weaker
  throughput/licensing guarantee than the text free providers; documented, not hidden.

## Scope

Changed: `qaops/execution/executor.py` (`_configured_model_is_free` nvidia branch).
Untouched: provider priority, the strategy engine, the selector, the image transport/
sidecar/provider implementation, Render configuration, and PRD/document behavior.
