# ADR-064: Capability-driven provider eligibility — remove `exclude_image_providers` (Phase C)

**Status:** Accepted · **Date:** 2026-08-23 · **Phase C** · **Relates to:** ADR-038 (image-aware selection), ADR-040B (per-stage selection), ADR-055 (NVIDIA free), ADR-063 (shared candidate builder); builds on Phase A (Gemini client image support) and Phase B (Gemini Flash marked image-capable)

## Context

Image capability was introduced when NVIDIA was the *only* image-capable provider. To keep that scarce, flaky, image-only provider off the downstream text stages of an image run, Phase 40B added an exclusionary selection rule, `exclude_image_providers`: on the downstream stages of an image-bearing run, any image-capable model was filtered out.

That rule encoded an assumption that is now obsolete. Phase A gave the Gemini client real image support, and Phase B marked `gemini-flash-latest` image-capable at the model level. Image capability is no longer scarce or synonymous with one provider. With the old rule still in place, a multimodal provider (NVIDIA or Gemini) that is perfectly capable of a text/structured stage was being excluded from it purely for *also* supporting images — the opposite of what a capability-driven system should do. In an NVIDIA-only deployment the same rule dropped the only reachable provider from downstream text calls, leaving zero candidates.

The product requirement is now capability-driven: a provider is eligible for a stage if and only if it satisfies that stage's real requirements (text, images, structured output, context/output size). Capability determines *eligibility*; the existing provider chain order determines *preference*; the existing resilient mechanism handles *failover*. There must be no provider-specific branching and no hard-coded NVIDIA→Gemini chain.

## Decision

Remove the `exclude_image_providers` concept entirely and rely solely on the positive capability rules already present in the selector:

- `needs_images=True` → candidate must support images (unchanged).
- `needs_text=True` → candidate must support text (unchanged).
- `needs_structured_output=True` → candidate must support structured output (unchanged).

Concretely:

- **`selector.py`** — the `exclude_image_providers` field is deleted from `StageRequirements`, along with its filter branch. No inert compatibility field is left behind.
- **`executor.py`** — `_requirements()` now sets only `needs_images`, and only for the image-consuming stage; downstream stages express just their real (text/structured) requirements. The two synthetic-candidate exclusion guards are removed. Provider chain order, priorities, failover accounting, retry budgets, and image transport are untouched.
- **`clarification/service.py`** — the gap-analysis call drops the exclusion; the initial image analysis keeps `needs_images=has_images`. Clarification uses the same capability-driven rules as the main pipeline, with no hard-coded provider.

Provider *ordering* is deliberately unchanged (ADR boundary): the existing chain places Gemini ahead of NVIDIA, so under capability-driven selection Gemini may be selected before NVIDIA for image stages. This is accepted; there is no NVIDIA-first special case. If NVIDIA (or any capable provider) is selected and fails, the existing generic failover moves to the next eligible capable provider.

## Consequences

- A multimodal provider now participates in **both** image stages and downstream text/structured stages — image capability is never itself a reason for exclusion.
- Image stages still require image capability, so genuinely text-only providers remain correctly excluded from them; the no-silent-drop guarantee holds.
- Text/PRD runs are functionally unchanged: no image stage means `needs_images=False` throughout and no exclusion ever applied.
- An image run no longer classifies the whole run as "image-provider-only": downstream stages admit the full capable set (multimodal + text-only).
- Failover is fully generic; a new image-capable provider becomes eligible automatically, with no additional provider-specific branch.
- Tests that encoded the obsolete NVIDIA-only/exclusion behavior (Phase 38/39/40B and two clarification suites) were updated to assert the capability-driven contract. The Gemini client, Gemini catalogue (Phase B), NVIDIA client, provider priorities, chain order, transport abstractions, and all clarification state/agent/readiness logic are unchanged.
