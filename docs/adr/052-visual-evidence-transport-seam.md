# ADR-052: Visual evidence transport seam (Phase 36 Part 1)

**Status:** Accepted · **Date:** 2026-08-12 · **Phase 36 Part 1** · **Relates to:** ADR-002 (LLM boundary), ADR-051 (multiple ticket attachments)

## Context

Ingestion extracts text only, so a DOCX/PDF's UI mockups or a standalone screenshot
never reach the model, and generated requirements/test cases cannot be grounded in
visual design. We want true visual understanding (not OCR). Code inspection
established two decisive facts:

- **One LLM seam.** All six model-calling stages funnel through
  `run_structured_stage` in `qaops/pipelines/test_design/_support.py`.
- **One visual consumer.** Only the RequirementAnalyzer reads the raw source
  (`requirement_text=data.text`); every downstream stage consumes derived artifacts
  (requirements/scenarios/conditions) and never re-reads the source. Requirements are
  born in the analyzer, so grounding them visually grounds the whole pipeline
  transitively.

Therefore visual evidence needs to reach exactly one stage through exactly one seam -
not every stage.

## Decision (Part 1 - transport/plumbing only, no provider)

Introduce the multimodal transport and wire it to the analyzer, without adding any
provider that consumes images. Six locked decisions:

1. **`ImagePart`** (`qaops/llm/models.py`): a provider-agnostic image value -
   `media_type` (png/jpeg only), base64 `data`, `source_filename`, `order`, and
   optional `page`/`image_index` (for future embedded extraction). Additive.
2. **`LLMMessage.images: list[ImagePart] = []`**: ADDITIVE and optional; `content:
   str` is unchanged. Under `exclude_defaults` a text-only message has no `images`
   key, so pre-Phase-36 serialization and every existing construction are
   byte-identical.
3. **`EvidencePackage`** (`qaops/ingestion/evidence.py`): an internal ingestion/API
   value object carrying images with provenance and ordering. It travels ALONGSIDE
   `RequirementInput` (which is NOT modified) and is consumed only by the analyzer.
   It is NOT a domain/pipeline model and is never placed in `qaops/models/`.
4. **`run_structured_stage` seam**: gains an optional `images` parameter attached to
   the single user message. Only the analyzer passes it; all other stages omit it and
   build byte-identical requests.
5. **Analyzer plumbing**: `RequirementAnalyzer.run(data, evidence=None)` passes the
   package's ordered images through the seam. The default `None` keeps the existing
   call backward-compatible.
6. **Hard-fail, never silent drop**: if a request carries images but the provider does
   not declare image support, `generate_structured` raises `LLMProviderError` before
   calling the provider. Providers default to text-only via a `supports_images`
   convention read with `getattr(..., False)`, so no provider is modified in Part 1.

### Deliberately out of scope for Part 1 (separate, approved later)

No real provider (no Nemotron 3 Nano Omni, no Anthropic/Gemini vision), no OCR, no
image-attachment ingestion, no image upload UI, no PDF/DOCX loader changes, no
embedded-image extraction, and no change to pipeline topology.

## Consequences

- The transport for true visual grounding exists and is tested deterministically with
  the MockLLMClient (no live model): image-bearing requests are recorded and asserted;
  a duck-typed multimodal mock exercises the success path.
- Text-only runs are provably unchanged (byte-identical serialization; no images
  anywhere).
- Adding a multimodal provider later is now a localized change (implement
  `supports_images` + image-block mapping on one provider), with the seam and fallback
  already in place.

### Notes / minor deviations (both resolved, behavior-preserving)

- `supports_images` is a `getattr` convention enforced in `generate_structured` rather
  than a member of the `LLMClient` Protocol - adding it to the Protocol broke the
  runtime `test_satisfies_protocol` checks for every provider, so to keep providers
  untouched the capability is read defensively (absent -> text-only). A provider opts
  in later by defining the property.
- `ImagePart` is imported under `TYPE_CHECKING` in `evidence.py` (annotations are
  strings via `from __future__ import annotations`); runtime behavior is unaffected.

## Alternatives considered

- **Thread images through every stage**: rejected - only the analyzer reads the source;
  this would change all six stage contracts for no benefit.
- **`content: str | list[parts]`**: rejected - a bigger, riskier change; an additive
  optional `images` field preserves every existing text-only construction.
- **Enrich/extend `RequirementInput`**: rejected - `EvidencePackage` travels in
  parallel so the domain contract is untouched.
- **OCR to text**: rejected by requirement - the goal is visual understanding, not text
  recovery.
