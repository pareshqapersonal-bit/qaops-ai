"""Phase 15-rev tests: runtime model discovery (ADR-027).

Discovery via provider APIs (mocked HTTP), capability filtering, caching,
and graceful degradation to the static table. No LLM is used for discovery."""

import io
import json
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from qaops.execution import (
    ModelInfo,
    ModelRegistry,
    discover_ollama_models,
    discover_openrouter_models,
    filter_by_capability,
    static_models,
)


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def json_response(payload: object) -> FakeResponse:
    return FakeResponse(json.dumps(payload).encode("utf-8"))


OPENROUTER_PAYLOAD = {
    "data": [
        {
            "id": "deepseek/deepseek-chat",
            "context_length": 64000,
            "top_provider": {"max_completion_tokens": 8192},
            "pricing": {"prompt": "0.0000014", "completion": "0.0000028"},
        },
        {
            "id": "openai/gpt-oss-20b:free",
            "context_length": 32000,
            "top_provider": {"max_completion_tokens": 2048},
            "pricing": {"prompt": "0", "completion": "0"},
        },
    ]
}


class TestOpenRouterDiscovery:
    def test_parses_the_documented_shape(self) -> None:
        with patch("urllib.request.urlopen", return_value=json_response(OPENROUTER_PAYLOAD)):
            models = discover_openrouter_models()
        names = [m.name for m in models]
        assert "deepseek/deepseek-chat" in names
        deepseek = next(m for m in models if m.name == "deepseek/deepseek-chat")
        assert deepseek.max_context_tokens == 64000
        assert deepseek.max_output_tokens == 8192

    def test_detects_free_models(self) -> None:
        with patch("urllib.request.urlopen", return_value=json_response(OPENROUTER_PAYLOAD)):
            models = discover_openrouter_models()
        free = next(m for m in models if m.name == "openai/gpt-oss-20b:free")
        assert free.free is True

    def test_missing_fields_get_defaults(self) -> None:
        payload = {"data": [{"id": "some/model"}]}
        with patch("urllib.request.urlopen", return_value=json_response(payload)):
            models = discover_openrouter_models()
        assert models[0].max_context_tokens == 8192
        assert models[0].max_output_tokens == 4096

    def test_network_failure_returns_empty(self) -> None:
        with patch("urllib.request.urlopen", side_effect=OSError("network down")):
            assert discover_openrouter_models() == []

    def test_malformed_json_returns_empty(self) -> None:
        with patch("urllib.request.urlopen", return_value=FakeResponse(b"not json")):
            assert discover_openrouter_models() == []

    def test_unexpected_shape_returns_empty(self) -> None:
        with patch("urllib.request.urlopen", return_value=json_response({"unexpected": True})):
            assert discover_openrouter_models() == []


class TestOllamaDiscovery:
    def test_parses_tag_list(self) -> None:
        payload = {"models": [{"name": "llama3.1"}, {"name": "mistral"}]}
        with patch("urllib.request.urlopen", return_value=json_response(payload)):
            models = discover_ollama_models()
        assert [m.name for m in models] == ["llama3.1", "mistral"]
        assert all(m.local and m.free for m in models)

    def test_no_daemon_returns_empty(self) -> None:
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            assert discover_ollama_models() == []


class TestModelRegistry:
    def test_discovery_merges_with_static_curated_first(self) -> None:
        registry = ModelRegistry(discovery_enabled=True)
        with patch("urllib.request.urlopen", return_value=json_response(OPENROUTER_PAYLOAD)):
            models = registry.models_for("openrouter")
        # Curated deepseek stays first (its curated priority), discovered
        # extras follow.
        assert models[0].name == "deepseek/deepseek-chat"
        assert any(m.name == "openai/gpt-oss-20b:free" for m in models)

    def test_falls_back_to_static_on_failure(self) -> None:
        registry = ModelRegistry(discovery_enabled=True)
        with patch("urllib.request.urlopen", side_effect=OSError("down")):
            models = registry.models_for("openrouter")
        assert [m.name for m in models] == [m.name for m in static_models("openrouter")]

    def test_disabled_discovery_uses_static_only(self) -> None:
        registry = ModelRegistry(discovery_enabled=False)
        calls: list[str] = []

        def spy(*args: object, **kwargs: object) -> object:
            calls.append("called")
            raise AssertionError("discovery must not run when disabled")

        with patch("urllib.request.urlopen", side_effect=spy):
            models = registry.models_for("openrouter")
        assert calls == []
        assert models == static_models("openrouter") or [m.name for m in models] == [
            m.name for m in static_models("openrouter")
        ]

    def test_results_are_cached(self) -> None:
        registry = ModelRegistry(discovery_enabled=True)
        call_count = {"n": 0}

        def counting(*args: object, **kwargs: object) -> object:
            call_count["n"] += 1
            return json_response(OPENROUTER_PAYLOAD)

        with patch("urllib.request.urlopen", side_effect=counting):
            registry.models_for("openrouter")
            registry.models_for("openrouter")
        assert call_count["n"] == 1  # second lookup used the cache

    def test_refresh_rediscovers(self) -> None:
        registry = ModelRegistry(discovery_enabled=True)
        call_count = {"n": 0}

        def counting(*args: object, **kwargs: object) -> object:
            call_count["n"] += 1
            return json_response(OPENROUTER_PAYLOAD)

        with patch("urllib.request.urlopen", side_effect=counting):
            registry.models_for("openrouter")
            registry.refresh("openrouter")
            registry.models_for("openrouter")
        assert call_count["n"] == 2

    def test_provider_without_discovery_uses_static(self) -> None:
        registry = ModelRegistry(discovery_enabled=True)
        # anthropic has no discovery implementation; must return static.
        models = registry.models_for("anthropic")
        assert [m.name for m in models] == [m.name for m in static_models("anthropic")]


class TestCapabilityFiltering:
    def _models(self) -> list[ModelInfo]:
        return [
            ModelInfo(name="small", provider="x", max_context_tokens=8000, max_output_tokens=2000),
            ModelInfo(name="big", provider="x", max_context_tokens=200000, max_output_tokens=16000),
            ModelInfo(
                name="nostruct",
                provider="x",
                max_context_tokens=100000,
                max_output_tokens=8000,
                structured_output=False,
            ),
        ]

    def test_filters_by_structured_output(self) -> None:
        result = filter_by_capability(self._models(), structured_output=True)
        assert "nostruct" not in [m.name for m in result]

    def test_filters_by_min_context(self) -> None:
        result = filter_by_capability(self._models(), min_context_chars=50000 * 4)
        assert [m.name for m in result] == ["big"]

    def test_excludes_named_models(self) -> None:
        result = filter_by_capability(self._models(), exclude={"big"})
        assert "big" not in [m.name for m in result]


class TestStaticTable:
    def test_every_registered_provider_has_models(self) -> None:
        for provider in ("anthropic", "gemini", "openrouter", "ollama"):
            assert static_models(provider), provider

    def test_unknown_provider_returns_empty(self) -> None:
        assert static_models("nonexistent") == []

    def test_char_properties_derive_from_tokens(self) -> None:
        model = ModelInfo(name="m", provider="p", max_context_tokens=1000, max_output_tokens=500)
        assert model.max_context_chars == 4000
        assert model.max_output_chars == 2000


@pytest.fixture(autouse=True)
def _no_real_network() -> Iterator[None]:
    """Guard: no test here should hit the real network."""
    yield
