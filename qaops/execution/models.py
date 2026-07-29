"""Model registry - discovery, caching, and capability metadata (ADR-027).

Execution reasons over model *capabilities*, not model names. This registry
answers "what models can this provider give me, and what can each do", using
the provider's own API where one exists and a curated static table otherwise.

Discovery is deterministic and makes no LLM call - it queries a models
endpoint over HTTP. Every discovery path degrades to the static table on any
failure: an unreachable endpoint, a timeout, or a response whose shape differs
from what we expect. That is deliberate. Discovery sits in the execution path,
so a network problem must cost fidelity, never the run.

Refresh happens at startup and on explicit request. There is no background
scheduler: a CLI run lasts a minute, and a mid-run cache invalidation would
add moving parts for no benefit.
"""

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Discovery must never delay a run for long; the static table is right there.
_DISCOVERY_TIMEOUT_SECONDS = 6.0

# Rough characters-per-token, matching the chunking registry's assumption.
_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class ModelInfo:
    """One model and what it can do."""

    name: str
    provider: str
    max_context_tokens: int = 8_192
    max_output_tokens: int = 4_096
    structured_output: bool = True
    local: bool = False
    free: bool = False
    priority: int = 100
    notes: str = ""

    @property
    def max_context_chars(self) -> int:
        return self.max_context_tokens * _CHARS_PER_TOKEN

    @property
    def max_output_chars(self) -> int:
        return self.max_output_tokens * _CHARS_PER_TOKEN


# Curated fallback, used when discovery is unavailable or a provider exposes no
# models endpoint. Ordered by priority within each provider: the first entry is
# what that provider is asked for first.
_STATIC_MODELS: dict[str, tuple[ModelInfo, ...]] = {
    "anthropic": (
        ModelInfo(
            name="claude-sonnet-4-6",
            provider="anthropic",
            max_context_tokens=200_000,
            max_output_tokens=16_384,
            priority=10,
            notes="Prompts are tuned against Anthropic models (ADR-013)",
        ),
        ModelInfo(
            name="claude-haiku-4-5-20251001",
            provider="anthropic",
            max_context_tokens=200_000,
            max_output_tokens=8_192,
            priority=20,
            notes="Cheaper fallback",
        ),
    ),
    # Gemini exposes BOTH free and paid candidates (ADR-034): the 2.5-flash
    # tier has a genuine no-cost API free tier, while 2.5-pro moved behind
    # billing. Marking flash free (and pro not) is why Gemini must not be
    # classified wholesale as paid - eligibility is per-model.
    "gemini": (
        ModelInfo(
            name="gemini-2.5-flash",
            provider="gemini",
            max_context_tokens=1_000_000,
            max_output_tokens=8_192,
            free=True,
            priority=10,
        ),
        ModelInfo(
            name="gemini-2.5-pro",
            provider="gemini",
            max_context_tokens=1_000_000,
            max_output_tokens=16_384,
            free=False,
            priority=20,
        ),
    ),
    "openrouter": (
        ModelInfo(
            name="deepseek/deepseek-chat",
            provider="openrouter",
            max_context_tokens=64_000,
            max_output_tokens=8_192,
            priority=10,
            notes="Handles structured JSON reliably",
        ),
        ModelInfo(
            name="openai/gpt-4o-mini",
            provider="openrouter",
            max_context_tokens=128_000,
            max_output_tokens=16_384,
            priority=20,
        ),
        ModelInfo(
            name="anthropic/claude-3.5-sonnet",
            provider="openrouter",
            max_context_tokens=200_000,
            max_output_tokens=8_192,
            priority=30,
        ),
        ModelInfo(
            name="meta-llama/llama-3.3-70b-instruct",
            provider="openrouter",
            max_context_tokens=128_000,
            max_output_tokens=8_192,
            priority=40,
        ),
    ),
    "ollama": (
        ModelInfo(
            name="llama3.1",
            provider="ollama",
            max_context_tokens=128_000,
            max_output_tokens=4_096,
            local=True,
            free=True,
            priority=10,
        ),
    ),
    # Groq free tier (ADR-034). Model IDs verified from Groq's official
    # rate-limits documentation. All are free-tier eligible, so free=True. The
    # llama-3.1-8b safety net has the highest daily request ceiling (14.4K RPD)
    # and so ranks last by quality but is the most resilient to per-model RPD
    # exhaustion. Context/output token limits are conservative documented values.
    "groq": (
        ModelInfo(
            name="llama-3.3-70b-versatile",
            provider="groq",
            max_context_tokens=128_000,
            max_output_tokens=32_768,
            free=True,
            priority=10,
            notes="Best free Groq quality; 1K RPD / 12K TPM",
        ),
        ModelInfo(
            name="openai/gpt-oss-120b",
            provider="groq",
            max_context_tokens=128_000,
            max_output_tokens=32_768,
            free=True,
            priority=20,
            notes="Strong free alternative; 1K RPD / 8K TPM",
        ),
        ModelInfo(
            name="llama-3.1-8b-instant",
            provider="groq",
            max_context_tokens=128_000,
            max_output_tokens=8_192,
            free=True,
            priority=30,
            notes="High daily ceiling safety net; 14.4K RPD",
        ),
    ),
}


def static_models(provider: str) -> list[ModelInfo]:
    """The curated model list for a provider, in priority order."""
    return list(_STATIC_MODELS.get(provider.strip().casefold(), ()))


# --- discovery ---------------------------------------------------------------


def _http_get_json(url: str, headers: dict[str, str] | None = None) -> object | None:
    """GET a JSON document, returning None on any failure."""
    request = urllib.request.Request(url, headers=headers or {})  # noqa: S310 - fixed https URLs
    try:
        with urllib.request.urlopen(request, timeout=_DISCOVERY_TIMEOUT_SECONDS) as response:
            parsed: object = json.loads(response.read().decode("utf-8"))
            return parsed
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as exc:
        logger.info("model_discovery.failed url=%s error=%s", url, exc)
        return None


def discover_openrouter_models() -> list[ModelInfo]:
    """Query OpenRouter's public models endpoint.

    Returns an empty list on any failure, so the caller falls back to the
    static table. The response shape is documented as
    {"data": [{"id", "context_length", "top_provider": {"max_completion_tokens"},
    "pricing": {"prompt", "completion"}}, ...]}; anything unexpected is skipped
    rather than trusted.
    """
    payload = _http_get_json("https://openrouter.ai/api/v1/models")
    if not isinstance(payload, dict):
        return []
    entries = payload.get("data")
    if not isinstance(entries, list):
        return []

    models: list[ModelInfo] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("id")
        if not isinstance(name, str) or not name:
            continue
        context = entry.get("context_length")
        max_context = int(context) if isinstance(context, int | float) and context else 8_192
        top = entry.get("top_provider")
        max_output = 4_096
        if isinstance(top, dict):
            completion = top.get("max_completion_tokens")
            if isinstance(completion, int | float) and completion:
                max_output = int(completion)
        pricing = entry.get("pricing")
        free = False
        if isinstance(pricing, dict):
            prompt_cost = pricing.get("prompt")
            free = str(prompt_cost) in {"0", "0.0", "-1"} or name.endswith(":free")
        models.append(
            ModelInfo(
                name=name,
                provider="openrouter",
                max_context_tokens=max_context,
                max_output_tokens=max_output,
                free=free,
                priority=100,
            )
        )
    return models


def discover_ollama_models(host: str = "http://localhost:11434") -> list[ModelInfo]:
    """Query a local Ollama daemon's tag list, if one is running."""
    payload = _http_get_json(f"{host}/api/tags")
    if not isinstance(payload, dict):
        return []
    entries = payload.get("models")
    if not isinstance(entries, list):
        return []
    models: list[ModelInfo] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        models.append(
            ModelInfo(
                name=name,
                provider="ollama",
                max_context_tokens=128_000,
                max_output_tokens=4_096,
                local=True,
                free=True,
                priority=10,
            )
        )
    return models


# Providers with a discovery implementation. Others use the static table.
_DISCOVERY: dict[str, object] = {
    "openrouter": discover_openrouter_models,
    "ollama": discover_ollama_models,
}


class ModelRegistry:
    """Discovers and caches the models each provider can serve.

    Discovery runs once per provider per registry instance (a CLI run), and
    again only on explicit refresh. Results are merged with the static table so
    curated priority ordering survives: a discovered model already known
    statically keeps its curated metadata.
    """

    def __init__(self, *, discovery_enabled: bool = True) -> None:
        self._discovery_enabled = discovery_enabled
        self._cache: dict[str, list[ModelInfo]] = {}
        self._discovered_at: dict[str, float] = {}

    def models_for(self, provider: str) -> list[ModelInfo]:
        """Every model this provider can serve, best first."""
        key = provider.strip().casefold()
        cached = self._cache.get(key)
        if cached is not None:
            return list(cached)
        resolved = self._resolve(key)
        self._cache[key] = resolved
        self._discovered_at[key] = time.time()
        return list(resolved)

    def refresh(self, provider: str | None = None) -> None:
        """Drop cached results so the next lookup rediscovers."""
        if provider is None:
            self._cache.clear()
            self._discovered_at.clear()
            return
        key = provider.strip().casefold()
        self._cache.pop(key, None)
        self._discovered_at.pop(key, None)

    def discovered_at(self, provider: str) -> float | None:
        return self._discovered_at.get(provider.strip().casefold())

    def _resolve(self, provider: str) -> list[ModelInfo]:
        curated = static_models(provider)
        if not self._discovery_enabled:
            return curated

        discover = _DISCOVERY.get(provider)
        if discover is None:
            return curated

        discovered = discover()  # type: ignore[operator]
        if not discovered:
            logger.info("model_discovery.static_fallback provider=%s", provider)
            return curated

        # Curated entries first, in their curated order; then anything newly
        # discovered that we have no opinion about.
        known = {model.name for model in curated}
        extra = [model for model in discovered if model.name not in known]
        extra.sort(key=lambda model: model.name)
        logger.info(
            "model_discovery.ok provider=%s curated=%d discovered=%d",
            provider,
            len(curated),
            len(discovered),
        )
        return [*curated, *extra]


@dataclass
class ModelHealth:
    """Per-run health for one model. Owned by the executor, not the registry."""

    name: str
    available: bool = True
    reason: str = ""
    failures: int = 0

    def mark_unavailable(self, reason: str) -> None:
        self.available = False
        self.reason = reason

    def record_failure(self) -> None:
        self.failures += 1


def filter_by_capability(
    models: list[ModelInfo],
    *,
    structured_output: bool = True,
    min_context_chars: int = 0,
    min_output_chars: int = 0,
    exclude: set[str] | None = None,
) -> list[ModelInfo]:
    """Narrow a model list to those meeting the stated requirements."""
    excluded = exclude or set()
    return [
        model
        for model in models
        if model.name not in excluded
        and (not structured_output or model.structured_output)
        and model.max_context_chars >= min_context_chars
        and model.max_output_chars >= min_output_chars
    ]
