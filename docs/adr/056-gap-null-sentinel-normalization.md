# ADR-056: Normalize null-sentinel requirement IDs in gap_analyzer

**Status:** Accepted · **Date:** 2026-08-14 · **Phase 40A** · **Relates to:** Phase 40 review (per-stage selection, deferred)

## Context

A production image run failed at `gap_analyzer` with "Model referenced unknown
requirement IDs: ['null']. Known IDs: ['REQ-001'...'REQ-005']". requirement_analyzer
and business_rule_extractor had produced valid artifacts; the run stalled at stage 3,
so no scenarios or test cases were generated.

`ExtractedGap.requirement_id` is nullable by design (`str | None = None`) - a gap can
legitimately be requirement-agnostic. The validator correctly ignores a real JSON
null (`if requirement_id is not None`). The failure was that Nemotron emitted the
STRING `"null"` instead of JSON null; Pydantic accepted it as a valid non-empty str,
it passed the `is not None` filter, and it failed as an unknown ID.

This is a provider-agnostic robustness gap, independent of provider selection: any
model can serialize a null as `"null"`/`"none"`/`""`. The Phase 40 review identified
a separate architectural issue (run-level vs per-stage provider selection); that
per-stage redesign is deferred - this ADR covers only the robustness fix.

## Decision

Before requirement-ID validation in `gap_analyzer`, normalize unambiguous null
sentinels to real None: `"null"`, `"none"`, and empty/whitespace-only strings,
case-insensitively and whitespace-tolerantly. Only these exact sentinels are
normalized; any other string (including `"REQ-999"` and `"abc"`) is left intact and
still fails validation. The normalized value flows into both the validation set and
the constructed Gap, so a sentinel becomes a genuine null-referenced gap rather than
being dropped or rejected.

Implemented as a small module-level helper `_normalize_requirement_id` in
`qaops/pipelines/test_design/gaps.py`. No schema change (the field is already
nullable). No prompt change.

## Consequences

- A gap the model serialized as the string `"null"` is treated as null; the stage
  completes and downstream stages run.
- Real unknown IDs still fail validation - the guard is unchanged for genuine
  references; only unambiguous "no value" sentinels are coerced.
- Provider-agnostic: benefits any model (including Nemotron on the analyzer stage,
  which must run on Nemotron for image tickets and could emit the same sentinel).
- **No change to provider selection**: only gaps.py is touched. Phase 38 image-aware
  selection, Phase 39 NVIDIA free classification, provider priority/ordering, and
  `QAOPS_EXECUTION_STRATEGY=free_only` behavior are all unchanged. NVIDIA remains
  low-priority (60) and is not preferred for PRD/text runs.

## Scope

Changed: `qaops/pipelines/test_design/gaps.py` (normalization helper + applied before
validation). Untouched: executor/selector/registry, DesignService, NVIDIA client,
ImagePart/EvidencePackage/sidecar/image ingestion, the LLM abstraction, prompts, and
execution strategy. Per-stage provider selection (Phase 40B/Shape-3) is NOT
implemented here.
