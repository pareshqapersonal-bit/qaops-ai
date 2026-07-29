"""Provider registry - what each provider is and whether it can be used (ADR-026).

One table describing every provider QAOps can talk to: which environment
variable holds its key, whether it runs locally, what it costs relative to
others, and how reliably it produces structured output. The adaptive executor
asks this registry for candidates rather than consulting a hardcoded list, so
adding a provider - Ollama, LM Studio, Azure OpenAI - means adding a row here
and a client, with no change to the executor or any pipeline stage.

Capability, not just availability: the registry answers "is this provider
usable right now" *and* "what is it good at", so future execution decisions
(prefer a local model for a large document to preserve cloud credit) have the
data they need without another lookup table.

Health is deliberately NOT stored here. The registry describes static facts;
health is per-run state owned by the executor, because a provider exhausted in
one run should be retried in the next.
"""

import os
from dataclasses import dataclass, field


# Ordered so lower numbers are tried first when the user expresses no preference.
# Local providers rank ahead of paid ones: they cost nothing and cannot run out
# of credit. Within cloud providers, the more reliable structured-output
# producers rank higher, because a provider that fails schema validation wastes
# a whole stage's retries.
@dataclass(frozen=True)
class ProviderInfo:
    """Static description of one provider."""

    name: str
    key_variables: tuple[str, ...] = ()
    local: bool = False
    structured_output: bool = True
    priority: int = 100
    notes: str = ""

    @property
    def requires_key(self) -> bool:
        return bool(self.key_variables)

    def api_key_present(self, environ: dict[str, str] | None = None) -> bool:
        """True when this provider's credential is set (or none is needed)."""
        if not self.requires_key:
            return True
        source = os.environ if environ is None else environ
        return any(source.get(name, "").strip() for name in self.key_variables)


# Providers with no client implementation yet must not be offered for failover,
# however available their credentials appear. Ollama needs no API key, so
# without this it would always look usable.
_IMPLEMENTED = frozenset({"anthropic", "gemini", "openrouter", "groq", "mock"})


_REGISTRY: dict[str, ProviderInfo] = {
    "ollama": ProviderInfo(
        name="ollama",
        key_variables=(),
        local=True,
        structured_output=True,
        priority=10,
        notes="Local models; no credit limits, no network dependency",
    ),
    "anthropic": ProviderInfo(
        name="anthropic",
        key_variables=("ANTHROPIC_API_KEY",),
        structured_output=True,
        priority=20,
        notes="Prompts are tuned against these models (ADR-013)",
    ),
    "openrouter": ProviderInfo(
        name="openrouter",
        key_variables=("OPENROUTER_API_KEY",),
        structured_output=True,
        priority=30,
        notes="Proxies many upstream models; quality varies by model",
    ),
    "gemini": ProviderInfo(
        name="gemini",
        key_variables=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        structured_output=True,
        priority=40,
    ),
    "groq": ProviderInfo(
        name="groq",
        key_variables=("GROQ_API_KEY",),
        structured_output=True,
        # Ranks ahead of openrouter as a free failover: a dedicated free tier
        # with strict json_schema output and its own independent account quota
        # (ADR-034). Free eligibility is per-model (its curated models are free),
        # so the free/paid distinction lives on ModelInfo, not here.
        priority=25,
        notes="Free tier, OpenAI-compatible, strict structured output",
    ),
    "mock": ProviderInfo(
        name="mock",
        key_variables=(),
        local=True,
        priority=999,
        notes="Test double; never selected automatically",
    ),
}

# Providers that must never be chosen by automatic discovery.
_NOT_AUTO_SELECTABLE = frozenset({"mock"})


def get_provider(name: str) -> ProviderInfo | None:
    """Look up one provider's description, or None if unknown."""
    return _REGISTRY.get(name.strip().casefold())


def all_providers() -> list[ProviderInfo]:
    """Every registered provider, in priority order."""
    return sorted(_REGISTRY.values(), key=lambda info: (info.priority, info.name))


def available_providers(environ: dict[str, str] | None = None) -> list[ProviderInfo]:
    """Registered providers usable right now, in priority order.

    Usable means: a client implementation exists, credentials are present, and
    the provider is not a test double. Ollama is registered so the strategy can
    reason about it, but is excluded until its client lands - otherwise it
    would always appear available, since it needs no API key.
    """
    return [
        info
        for info in all_providers()
        if info.name in _IMPLEMENTED
        and info.name not in _NOT_AUTO_SELECTABLE
        and info.api_key_present(environ)
    ]


def key_variables_for(name: str) -> tuple[str, ...]:
    """The environment variables that hold this provider's key."""
    info = get_provider(name)
    return info.key_variables if info is not None else ()


@dataclass
class ProviderHealth:
    """Per-run health for one provider. Owned by the executor, not the registry."""

    name: str
    available: bool = True
    reason: str = ""
    failures: int = 0
    attempts: list[str] = field(default_factory=list)

    def mark_unavailable(self, reason: str) -> None:
        self.available = False
        self.reason = reason

    def record_failure(self, reason: str) -> None:
        self.failures += 1
        self.attempts.append(reason)
