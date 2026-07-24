"""Provider capability metadata (ADR-021).

Chunk sizing depends on what a model can practically handle. That knowledge
lives here, in one table keyed by provider and model, rather than being
hardcoded across the codebase or bolted onto the LLMClient protocol - the
protocol stays a pure completion boundary (ADR-002), and adding a provider
means adding a row here and nothing else.

The governing constraint is OUTPUT capacity, not input. Every real failure
observed in practice was `stop_reason=length` during generation: the model
read the document fine but could not emit all the extracted requirements in
one response. Input context windows are far larger than the documents QAOps
handles, so sizing against them would produce chunks that reliably truncate.

`max_output_tokens` here is the model's practical output ceiling, which is
often much smaller than its advertised context window.
"""

from dataclasses import dataclass

# Rough characters-per-token for English prose. Deliberately conservative:
# underestimating tokens-per-character yields smaller, safer chunks.
CHARS_PER_TOKEN = 4

# Used when a provider or model is not in the table. Chosen to match the
# smallest common output ceiling rather than an optimistic average, so an
# unknown model errs toward more chunks rather than truncation.
DEFAULT_MAX_OUTPUT_TOKENS = 4096


@dataclass(frozen=True)
class ModelCapability:
    """What one model can practically produce in a single response."""

    max_output_tokens: int
    notes: str = ""

    @property
    def max_output_chars(self) -> int:
        return self.max_output_tokens * CHARS_PER_TOKEN


# Per-provider defaults, applied when a specific model is not listed.
_PROVIDER_DEFAULTS: dict[str, ModelCapability] = {
    "anthropic": ModelCapability(max_output_tokens=8192, notes="Claude models"),
    "gemini": ModelCapability(max_output_tokens=8192, notes="Gemini Flash/Pro"),
    "openrouter": ModelCapability(
        max_output_tokens=4096,
        notes="Varies widely by upstream model; conservative default",
    ),
    "ollama": ModelCapability(max_output_tokens=4096, notes="Local models"),
    "mock": ModelCapability(max_output_tokens=8192, notes="Test double"),
}

# Specific models whose capacity differs materially from the provider default.
# Keys are matched case-insensitively against the configured model string.
_MODEL_OVERRIDES: dict[str, ModelCapability] = {
    "claude-sonnet-4-6": ModelCapability(max_output_tokens=16384),
    "claude-opus-4": ModelCapability(max_output_tokens=16384),
    "gemini-2.5-flash": ModelCapability(max_output_tokens=8192),
    "gemini-2.5-pro": ModelCapability(max_output_tokens=16384),
    "deepseek/deepseek-chat": ModelCapability(
        max_output_tokens=8192, notes="Handles structured JSON well"
    ),
    "openai/gpt-4o-mini": ModelCapability(max_output_tokens=16384),
    "openai/gpt-oss-20b:free": ModelCapability(
        max_output_tokens=2048, notes="Small free-tier model; frequently truncates"
    ),
}


def capability_for(provider: str, model: str) -> ModelCapability:
    """Resolve the capability for a provider/model pair.

    Lookup order: exact model override, then provider default, then the
    conservative global default. Never raises - an unknown provider yields
    smaller chunks rather than an error.
    """
    override = _MODEL_OVERRIDES.get(model.strip().casefold())
    if override is not None:
        return override
    provider_default = _PROVIDER_DEFAULTS.get(provider.strip().casefold())
    if provider_default is not None:
        return provider_default
    return ModelCapability(max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS, notes="Unknown provider")


def known_providers() -> list[str]:
    """Providers with explicit capability metadata, for diagnostics."""
    return sorted(_PROVIDER_DEFAULTS)
