# ADR-057: Per-stage provider selection for image runs

**Status:** Accepted · **Date:** 2026-08-14 · **Phase 40B** · **Relates to:** ADR-052 (visual evidence seam), ADR-054 (image-aware selection), ADR-055 (NVIDIA free), Phase 40 review

## Context

Phase 38 made provider selection image-aware at the RUN level: any image in a run set
`requires_images=True` for the whole executor, forcing every stage onto an
image-capable provider (NVIDIA). Production runs showed this is too coarse: after
requirement_analyzer extracts requirements from the images, the downstream stages
(business_rule_extractor, gap_analyzer, scenario_generator, ...) consume only derived
JSON - no images - yet were still pinned to NVIDIA. When NVIDIA's free endpoint
returned 500 "EngineCore" errors on the heavier downstream prompts, the run failed
with no eligible text provider to fail over to (they were all filtered out as
non-image-capable). requirement_analyzer is the sole image consumer (verified:
evidence is bound only to ChunkedRequirementAnalyzer in the builder).

## Decision

Select the provider PER STAGE, not per run (Shape 1-minimal from the Phase 40 review):

- **Image-consuming stage** (requirement_analyzer, when the run carries images):
  requires an image-capable provider (`needs_images=True`). Phase 36A hard-fail
  preserved - if none exists, fail clearly; never silently drop images or fall back
  to a text-only provider.
- **Downstream stages of an image run**: EXCLUDE the image provider
  (`exclude_image_providers=True`) and use the normal text chain/strategy. NVIDIA is
  reserved for the one stage that needs it and kept off the flaky path for the rest
  (approved decision 3 - stricter than "don't require images": downstream must never
  select NVIDIA, even as a last-resort text failover).
- **Text/PRD runs**: no image stage -> every stage computes `needs_images=False` and
  `exclude_image_providers=False`, so selection, ordering, and fallback are identical
  to before. NVIDIA remains a normal low-priority (60) candidate, not preferred.

The image-consuming stage is identified by NAME from the orchestration layer
(`DesignService` passes `image_stage_name="requirement_analyzer"` and the ordered
`stage_names`), not a hard-coded index and not a change to the stage protocol or
RequirementAnalyzer (approved decision 2c). The executor tracks the current stage and
computes `StageRequirements` per stage; on a stage boundary it re-selects the provider
only when the current one no longer serves the new stage (a no-op for text runs).

## Consequences

- Image run: requirement_analyzer -> NVIDIA (with the image, byte-identical);
  business_rule_extractor / gap_analyzer / scenario_generator / test_case_generator /
  coverage -> normal text chain (Groq/Gemini). Downstream NVIDIA 500s can no longer
  block the pipeline because downstream never touches NVIDIA.
- Recovery: the image stage recovers only to image-capable providers; downstream
  recovers across the normal text chain; NVIDIA is never a downstream candidate.
- Resume: resuming at a downstream stage no longer requires NVIDIA (the run's images
  don't force it) - fixes a latent bug where resume re-required the image provider.
- free_only: analyzer -> NVIDIA (free per ADR-055); downstream -> free text chain,
  unchanged. Existing PRD/text provider ordering unchanged.
- Budgets/health accounting unchanged (already per-stage). No change to ImagePart,
  EvidencePackage, sidecar, RequirementAnalyzer, pipeline stages, the LLM abstraction,
  provider clients, image ingestion, execute_run, API, or frontend.
- Phase 40A (gap_analyzer null-sentinel normalization) is untouched and orthogonal.

## Scope

Changed: `qaops/execution/selector.py` (`exclude_image_providers` requirement +
filter), `qaops/execution/executor.py` (per-stage selection: `image_stage_name` +
`stage_names`, current-stage tracking, stage-aware `_requirements`, per-stage
re-selection, image-exclusion in candidate paths), `qaops/services/design_service.py`
(pass image-stage name + stage names). Protected components untouched.

## Alternatives (from the review)

- Shape 2 (analyzer special-case + run-level rest): rejected - Shape 1-minimal is one
  uniform per-stage rule, cleaner.
- Shape 3 (full per-stage re-selection for every run): rejected - larger blast radius,
  risks text-run drift.
- "Downstream may use NVIDIA as last resort": rejected by decision 3 - downstream must
  never touch NVIDIA, both to reserve capacity and to isolate from its instability.
