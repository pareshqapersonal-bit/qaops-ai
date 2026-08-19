"""Shared candidate-model building (Phase 41C-4, extracted from the executor).

Single source of truth for "what models can a provider give, and what can each
do" - discovery, synthetic-candidate construction, and the free/image/text/
structured capability flags. Both the AdaptiveExecutor and the clarification
resilient-call helper consume this so provider-selection rules can never diverge.

This is a behaviour-preserving extraction of the executor's former private
methods (`_synthetic_candidate`, `_configured_model_is_free`,
`_provider_supports_images`, the `_candidates` synthetic branch) and the
`_MODEL_FIELD` map. No rule is approximated: Gemini is free only for flash tiers,
NVIDIA is free (ADR-055), local providers are free, everything else defaults to
not-free. `select_candidates()` remains the separate ranking/filtering primitive;
this module only builds the model list it ranks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qaops.execution.models import ModelInfo

if TYPE_CHECKING:
    from collections.abc import Sequence

    from qaops.config import QAOpsSettings
    from qaops.execution.models import ModelRegistry
    from qaops.execution.registry import ProviderInfo

# The settings field carrying each provider's configured model name. Canonical
# home for this map (the executor imports it from here).
MODEL_FIELD: dict[str, str] = {
    "anthropic": "model",
    "gemini": "gemini_model",
    "openrouter": "openrouter_model",
    "groq": "groq_model",
    "nvidia": "nvidia_model",
}


def configured_model(settings: QAOpsSettings, provider: str) -> str:
    """The configured model name for a provider, or '' if none/unknown."""
    field_name = MODEL_FIELD.get(provider)
    if field_name is None:
        return ""
    return str(getattr(settings, field_name, ""))


def configured_model_is_free(
    settings: QAOpsSettings, provider: str, providers: Sequence[ProviderInfo]
) -> bool:
    """Free eligibility of a provider's single configured model (canonical rule).

    Gemini's free tier serves its flash models via an API key at no cost, so a
    configured gemini flash/flash-lite model is free-eligible - Gemini is NOT
    wholesale paid merely because paid Gemini usage also exists (ADR-034).
    NVIDIA's Nemotron models are free (zero monetary cost per call) through
    build.nvidia.com (ADR-055; the free tier is rate-limited and dev/eval-only,
    but "free" here is cost-based). Local providers are always free. Anthropic and
    other remote providers default to not-free unless a registry model said so.
    """
    lowered = configured_model(settings, provider).casefold()
    if provider == "gemini":
        return "flash" in lowered
    if provider == "nvidia":
        return True
    info = next((p for p in providers if p.name == provider), None)
    return bool(info and info.local)


def provider_supports_images(provider: str, providers: Sequence[ProviderInfo]) -> bool:
    """Whether the registry marks this provider as image-capable (Phase 38)."""
    info = next((p for p in providers if p.name == provider), None)
    return bool(info and info.images)


def synthetic_candidate(
    settings: QAOpsSettings, provider: str, name: str, providers: Sequence[ProviderInfo]
) -> ModelInfo:
    """A single-model candidate for a provider with no catalogue.

    Its free flag reflects the provider's configured-model eligibility, so
    FREE_ONLY/FREE_FIRST treat e.g. a gemini flash model as free and an anthropic
    model as paid. Image capability comes from the provider descriptor.
    """
    return ModelInfo(
        name=name,
        provider=provider,
        free=configured_model_is_free(settings, provider, providers),
        images_supported=provider_supports_images(provider, providers),
    )


def models_for_provider(
    settings: QAOpsSettings,
    provider: ProviderInfo,
    registry: ModelRegistry,
    providers: Sequence[ProviderInfo],
) -> list[ModelInfo]:
    """Raw candidate models for ONE provider: discovered, else one synthetic.

    Returns the discovered/curated models when the registry has a catalogue for
    the provider, otherwise a single synthetic candidate (configured model name if
    set, else '<provider>-default') with canonical free/image flags. Filtering and
    ranking against stage requirements is left to select_candidates(); this returns
    the unfiltered building blocks so callers keep their own exclusion/budget logic.
    """
    discovered = registry.models_for(provider.name)
    if discovered:
        return discovered
    name = configured_model(settings, provider.name) or f"{provider.name}-default"
    return [synthetic_candidate(settings, provider.name, name, providers)]


def build_candidate_models(
    *,
    providers: Sequence[ProviderInfo],
    settings: QAOpsSettings,
    registry: ModelRegistry,
) -> list[ModelInfo]:
    """The flat candidate-model list across a provider chain, in provider order.

    Discovered models where a catalogue exists, else one synthetic candidate per
    provider. Capability flags (free/images/text/structured) are set by the single
    canonical rule above. select_candidates() then ranks/filters this list for a
    stage's requirements. Shared by the executor and the clarification helper so the
    two paths cannot diverge.
    """
    models: list[ModelInfo] = []
    for provider in providers:
        models.extend(models_for_provider(settings, provider, registry, providers))
    return models


def settings_for_model(settings: QAOpsSettings, model: ModelInfo) -> QAOpsSettings:
    """Inject a chosen provider+model into settings (canonical _settings_for)."""
    update: dict[str, object] = {"provider": model.provider}
    field = MODEL_FIELD.get(model.provider)
    if field is not None and model.name:
        update[field] = model.name
    return settings.model_copy(update=update)
