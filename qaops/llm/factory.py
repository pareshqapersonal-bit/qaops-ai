"""Configuration-driven provider selection (ADR-013).

create_client() is the single place that maps QAOPS_PROVIDER to a
concrete LLMClient. Composition code (eval scripts, the future CLI)
calls it instead of constructing clients directly, so switching
providers is purely an environment change:

    QAOPS_PROVIDER=anthropic   (default; uses QAOPS_MODEL)
    QAOPS_PROVIDER=gemini      (uses QAOPS_GEMINI_MODEL; needs the
                                'gemini' extra and GEMINI_API_KEY)

The 'mock' provider is deliberately rejected here: mocks are scripted
per-test and must be constructed explicitly, never ambiently.
"""

from qaops.config import QAOpsSettings
from qaops.core.errors import ConfigurationError
from qaops.llm.anthropic_client import AnthropicClient
from qaops.llm.client import LLMClient


def create_client(settings: QAOpsSettings) -> LLMClient:
    """Build the LLMClient selected by settings.provider."""
    if settings.provider == "anthropic":
        return AnthropicClient(model=settings.model)
    if settings.provider == "gemini":
        try:
            from qaops.llm.gemini_client import GeminiClient
        except ImportError as exc:
            msg = (
                "Provider 'gemini' selected but the google-genai SDK is not "
                "installed. Install the optional extra: pip install 'qaops-ai[gemini]'"
            )
            raise ConfigurationError(msg) from exc
        return GeminiClient(model=settings.gemini_model)
    if settings.provider == "mock":
        msg = (
            "Provider 'mock' cannot be created by the factory; construct "
            "MockLLMClient with a scripted response list in your test instead."
        )
        raise ConfigurationError(msg)
    msg = f"Unknown provider {settings.provider!r}"  # unreachable: settings validates
    raise ConfigurationError(msg)
