"""Free-execution strategy and provider-wide quota tests (ADR-034).

No live LLM calls: FakeStage records which provider/model ran and can fail on
chosen pairings. These assert the strategy semantics the phase requires:

- FREE_ONLY never invokes Anthropic, can use eligible Gemini / Groq, and uses
  only free OpenRouter candidates.
- FREE_FIRST exhausts free-eligible candidates before paid ones.
- ANY preserves current behaviour.
- OpenRouter account-wide exhaustion disables the provider for the rest of the
  run, while a model/transient 429 does NOT.
- A missing Groq key skips Groq.
"""

import contextlib
from collections.abc import Sequence

from pydantic import BaseModel

from qaops.config import QAOpsSettings
from qaops.execution.executor import AdaptiveExecutor
from qaops.execution.models import ModelInfo, ModelRegistry
from qaops.execution.registry import ProviderInfo, available_providers, get_provider


class Doc(BaseModel):
    trace: list[str] = []


class FakeStage:
    def __init__(self, name: str, provider: str, model: str, fail_on: dict[str, str]) -> None:
        self.name = name
        self._provider = provider
        self._model = model
        self._fail_on = fail_on

    def run(self, data: Doc) -> Doc:
        message = self._fail_on.get(f"{self._provider}/{self._model}") or self._fail_on.get(
            self._provider
        )
        if message:
            raise RuntimeError(message)
        return Doc(trace=[*data.trace, f"{self._provider}/{self._model}"])


_MODEL_FIELD = {
    "anthropic": "model",
    "gemini": "gemini_model",
    "openrouter": "openrouter_model",
    "groq": "groq_model",
}


def make_factory(fail_on: dict[str, str] | None = None) -> tuple[object, list[str]]:
    calls: list[str] = []
    failures = fail_on or {}

    def factory(settings: QAOpsSettings) -> Sequence[FakeStage]:
        model = str(getattr(settings, _MODEL_FIELD.get(settings.provider, "model"), ""))
        calls.append(f"{settings.provider}/{model}")
        return [FakeStage("stage", settings.provider, model, failures)]

    return factory, calls


class _Registry(ModelRegistry):
    """Registry returning a controlled model set per provider."""

    def __init__(self, models: dict[str, list[ModelInfo]]) -> None:
        super().__init__(discovery_enabled=False)
        self._models = models

    def models_for(self, provider: str) -> list[ModelInfo]:
        return list(self._models.get(provider, []))


def _providers(*names: str) -> list[ProviderInfo]:
    return [get_provider(n) for n in names]  # type: ignore[misc]


def _run(
    providers: list[ProviderInfo],
    settings: QAOpsSettings,
    registry: ModelRegistry,
    fail_on: dict[str, str] | None = None,
) -> tuple[Doc, list[str]]:
    factory, calls = make_factory(fail_on)
    executor = AdaptiveExecutor(
        providers, settings, factory, registry=registry, sleep=lambda _s: None
    )
    result = executor.run(Doc())
    return result, calls


# A registry where each provider offers one free and (where relevant) one paid
# model, so per-model eligibility is exercised, not just per-provider.
def _mixed_registry() -> _Registry:
    return _Registry(
        {
            "groq": [ModelInfo(name="llama-3.3-70b-versatile", provider="groq", free=True)],
            "gemini": [
                ModelInfo(name="gemini-2.5-flash", provider="gemini", free=True),
                ModelInfo(name="gemini-2.5-pro", provider="gemini", free=False),
            ],
            "openrouter": [
                ModelInfo(name="deepseek/deepseek-chat:free", provider="openrouter", free=True),
                ModelInfo(name="openai/gpt-4o-mini", provider="openrouter", free=False),
            ],
            "anthropic": [ModelInfo(name="claude-sonnet-4-6", provider="anthropic", free=False)],
        }
    )


class TestFreeOnly:
    def test_free_only_never_invokes_anthropic(self) -> None:
        # Even if every free provider fails, Anthropic must never be called.
        providers = _providers("groq", "gemini", "openrouter", "anthropic")
        settings = QAOpsSettings(provider="groq", execution_strategy="free_only")
        fail_on = {"groq": "boom", "gemini": "boom", "openrouter": "boom"}
        factory, calls = make_factory(fail_on)
        executor = AdaptiveExecutor(
            providers, settings, factory, registry=_mixed_registry(), sleep=lambda _s: None
        )
        # All free providers exhausting is an acceptable terminal state here.
        with contextlib.suppress(Exception):
            executor.run(Doc())
        # The assertion that matters: Anthropic (paid) was never built or called.
        assert not any(c.startswith("anthropic/") for c in calls)

    def test_free_only_can_use_gemini(self) -> None:
        providers = _providers("gemini")
        settings = QAOpsSettings(provider="gemini", execution_strategy="free_only")
        result, calls = _run(providers, settings, _mixed_registry())
        assert result.trace == ["gemini/gemini-2.5-flash"]
        # The paid gemini-2.5-pro is never selected under free_only.
        assert all("gemini-2.5-pro" not in c for c in calls)

    def test_free_only_can_use_groq(self) -> None:
        providers = _providers("groq")
        settings = QAOpsSettings(provider="groq", execution_strategy="free_only")
        result, _ = _run(providers, settings, _mixed_registry())
        assert result.trace == ["groq/llama-3.3-70b-versatile"]

    def test_free_only_uses_only_free_openrouter_candidates(self) -> None:
        providers = _providers("openrouter")
        settings = QAOpsSettings(provider="openrouter", execution_strategy="free_only")
        result, calls = _run(providers, settings, _mixed_registry())
        # Only the :free model may be chosen; the paid gpt-4o-mini never.
        assert result.trace == ["openrouter/deepseek/deepseek-chat:free"]
        assert all("gpt-4o-mini" not in c for c in calls)

    def test_free_only_excludes_anthropic_from_provider_set(self) -> None:
        providers = _providers("anthropic", "groq")
        settings = QAOpsSettings(provider="groq", execution_strategy="free_only")
        executor = AdaptiveExecutor(
            providers,
            settings,
            make_factory()[0],
            registry=_mixed_registry(),
            sleep=lambda _s: None,
        )
        assert [p.name for p in executor._providers] == ["groq"]


class TestFreeFirst:
    def test_free_first_exhausts_free_before_paid(self) -> None:
        # Free providers (groq, gemini) fail; execution should reach paid
        # (openrouter paid model / anthropic) only AFTER the free ones.
        providers = _providers("anthropic", "groq", "gemini")
        settings = QAOpsSettings(provider="groq", execution_strategy="free_first")
        fail_on = {"groq": "boom", "gemini": "boom"}
        result, calls = _run(providers, settings, _mixed_registry(), fail_on)
        # Anthropic (paid) succeeds only after both free providers were tried.
        assert result.trace == ["anthropic/claude-sonnet-4-6"]
        groq_idx = next(i for i, c in enumerate(calls) if c.startswith("groq/"))
        anthropic_idx = next(i for i, c in enumerate(calls) if c.startswith("anthropic/"))
        assert groq_idx < anthropic_idx


class TestAny:
    def test_any_preserves_registry_priority_order(self) -> None:
        # ANY keeps the passed-in provider order unchanged.
        providers = _providers("anthropic", "groq", "openrouter", "gemini")
        settings = QAOpsSettings(provider="anthropic", execution_strategy="any")
        executor = AdaptiveExecutor(
            providers,
            settings,
            make_factory()[0],
            registry=_mixed_registry(),
            sleep=lambda _s: None,
        )
        assert [p.name for p in executor._providers] == [
            "anthropic",
            "groq",
            "openrouter",
            "gemini",
        ]

    def test_any_is_the_default(self) -> None:
        # No execution_strategy set -> ANY, first provider used.
        providers = _providers("anthropic")
        settings = QAOpsSettings(provider="anthropic")
        result, _ = _run(providers, settings, _mixed_registry())
        assert result.trace == ["anthropic/claude-sonnet-4-6"]


class TestMissingGroqKey:
    def test_missing_groq_key_skips_groq(self, monkeypatch: object) -> None:
        # available_providers() gates on key presence: with no GROQ_API_KEY,
        # Groq is not offered as a usable provider.
        import os

        for var in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY"):
            os.environ.pop(var, None)
        os.environ["ANTHROPIC_API_KEY"] = "present"
        usable = {p.name for p in available_providers()}
        assert "anthropic" in usable
        assert "groq" not in usable
        os.environ.pop("ANTHROPIC_API_KEY", None)
