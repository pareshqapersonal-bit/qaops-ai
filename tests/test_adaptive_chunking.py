"""Phase 11.1 tests: adaptive chunking (ADR-021).

Covers the capability registry, the ChunkStrategy decision logic, automatic
bypass for small documents, manual fixed override, determinism, and
provider-specific calculations. No LLM calls: every decision is a pure
function of document length and configuration."""

import json

import pytest

from qaops.config import QAOpsSettings
from qaops.llm import MockLLMClient, PromptLoader
from qaops.models import RequirementInput
from qaops.pipelines.chunking import ChunkedRequirementAnalyzer, ChunkStrategy, capability_for
from qaops.pipelines.chunking.capabilities import (
    CHARS_PER_TOKEN,
    DEFAULT_MAX_OUTPUT_TOKENS,
    known_providers,
)
from qaops.pipelines.chunking.strategy import MIN_CHUNK_SIZE


def extraction(*titles: str) -> str:
    return json.dumps(
        {"requirements": [{"title": t, "description": f"description of {t}"} for t in titles]}
    )


class TestCapabilityRegistry:
    def test_model_override_wins_over_provider_default(self) -> None:
        # gpt-oss-20b:free is far weaker than the openrouter default.
        override = capability_for("openrouter", "openai/gpt-oss-20b:free")
        default = capability_for("openrouter", "some-unlisted-model")
        assert override.max_output_tokens < default.max_output_tokens

    def test_provider_default_used_for_unlisted_model(self) -> None:
        capability = capability_for("anthropic", "claude-not-in-table")
        assert capability.max_output_tokens == 8192

    def test_unknown_provider_falls_back_conservatively(self) -> None:
        capability = capability_for("nonexistent", "whatever")
        assert capability.max_output_tokens == DEFAULT_MAX_OUTPUT_TOKENS

    def test_lookup_is_case_insensitive(self) -> None:
        assert capability_for("OpenRouter", "DeepSeek/DeepSeek-Chat") == capability_for(
            "openrouter", "deepseek/deepseek-chat"
        )

    def test_max_output_chars_derives_from_tokens(self) -> None:
        capability = capability_for("anthropic", "claude-sonnet-4-6")
        assert capability.max_output_chars == capability.max_output_tokens * CHARS_PER_TOKEN

    def test_known_providers_listed(self) -> None:
        providers = known_providers()
        for expected in ("anthropic", "gemini", "openrouter", "ollama"):
            assert expected in providers

    def test_never_raises_for_any_input(self) -> None:
        assert capability_for("", "").max_output_tokens > 0


class TestAdaptiveDecisions:
    def _strategy(
        self, provider: str = "openrouter", model: str = "deepseek/deepseek-chat"
    ) -> ChunkStrategy:
        return ChunkStrategy(provider=provider, model=model, safety_margin=0.8)

    def test_small_document_bypasses_chunking(self) -> None:
        decision = self._strategy().decide(1000)
        assert decision.should_chunk is False
        assert "fits within" in decision.reason

    def test_real_prd_size_bypasses_on_capable_model(self) -> None:
        # The 7656-char PRD that succeeded unchunked in practice.
        decision = self._strategy().decide(7656)
        assert decision.should_chunk is False

    def test_very_large_document_chunks(self) -> None:
        decision = self._strategy().decide(120_000)
        assert decision.should_chunk is True
        assert decision.chunk_size > 0
        assert decision.chunk_overlap > 0
        assert decision.chunk_overlap < decision.chunk_size

    def test_weak_model_chunks_even_a_small_document(self) -> None:
        weak = self._strategy(model="openai/gpt-oss-20b:free")
        strong = self._strategy(model="deepseek/deepseek-chat")
        assert weak.capacity_chars() < strong.capacity_chars()
        assert weak.decide(7656).should_chunk is True
        assert strong.decide(7656).should_chunk is False

    def test_provider_specific_capacity(self) -> None:
        anthropic = ChunkStrategy(
            provider="anthropic", model="claude-sonnet-4-6", safety_margin=0.8
        )
        openrouter = self._strategy(model="openai/gpt-oss-20b:free")
        assert anthropic.capacity_chars() > openrouter.capacity_chars()

    def test_safety_margin_shrinks_capacity(self) -> None:
        cautious = ChunkStrategy(provider="anthropic", model="claude-sonnet-4-6", safety_margin=0.5)
        relaxed = ChunkStrategy(provider="anthropic", model="claude-sonnet-4-6", safety_margin=1.0)
        assert cautious.capacity_chars() < relaxed.capacity_chars()

    def test_capacity_never_below_minimum(self) -> None:
        tiny = ChunkStrategy(provider="nonexistent", model="x", safety_margin=0.01)
        assert tiny.capacity_chars() >= MIN_CHUNK_SIZE

    def test_rejects_invalid_safety_margin(self) -> None:
        for bad in (0.0, -0.5, 1.5):
            with pytest.raises(ValueError, match="safety_margin"):
                ChunkStrategy(provider="anthropic", model="m", safety_margin=bad)

    def test_deterministic(self) -> None:
        strategy = self._strategy()
        assert strategy.decide(50_000) == strategy.decide(50_000)

    def test_is_adaptive_flag(self) -> None:
        assert self._strategy().is_adaptive is True


class TestFixedOverride:
    def _fixed(self, size: int = 8000, overlap: int = 500) -> ChunkStrategy:
        return ChunkStrategy(
            provider="openrouter",
            model="deepseek/deepseek-chat",
            safety_margin=0.8,
            fixed_chunk_size=size,
            fixed_chunk_overlap=overlap,
        )

    def test_uses_configured_size_verbatim(self) -> None:
        decision = self._fixed().decide(20_000)
        assert decision.should_chunk is True
        assert decision.chunk_size == 8000
        assert decision.chunk_overlap == 500
        assert "fixed strategy" in decision.reason

    def test_does_not_chunk_when_document_fits(self) -> None:
        assert self._fixed().decide(7656).should_chunk is False

    def test_ignores_adaptive_capacity(self) -> None:
        # A tiny fixed size chunks even though the model could handle more.
        decision = self._fixed(size=1000, overlap=100).decide(5000)
        assert decision.should_chunk is True
        assert decision.chunk_size == 1000

    def test_is_adaptive_flag_false(self) -> None:
        assert self._fixed().is_adaptive is False


class TestSettings:
    def test_adaptive_is_the_default(self) -> None:
        settings = QAOpsSettings()
        assert settings.chunking_strategy == "adaptive"
        assert settings.chunk_safety_margin == 0.8

    def test_fixed_strategy_accepted(self) -> None:
        settings = QAOpsSettings(chunking_strategy="fixed", chunk_size=8000)
        assert settings.chunking_strategy == "fixed"

    def test_unknown_strategy_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown chunking strategy"):
            QAOpsSettings(chunking_strategy="magic")

    def test_safety_margin_bounds_enforced(self) -> None:
        with pytest.raises(ValueError):
            QAOpsSettings(chunk_safety_margin=0.0)
        with pytest.raises(ValueError):
            QAOpsSettings(chunk_safety_margin=1.5)


class TestAnalyzerIntegration:
    def test_adaptive_bypass_makes_exactly_one_call(self) -> None:
        settings = QAOpsSettings(provider="anthropic", model="claude-sonnet-4-6")
        client = MockLLMClient([extraction("Login")])
        result = ChunkedRequirementAnalyzer(client, PromptLoader(), settings).run(
            RequirementInput(text="a short document", source_name="s.md")
        )
        assert client.call_count == 1
        assert [r.id for r in result.requirements] == ["REQ-001"]

    def test_adaptive_chunks_a_large_document(self) -> None:
        settings = QAOpsSettings(provider="anthropic", model="claude-sonnet-4-6")
        strategy = ChunkStrategy(provider="anthropic", model="claude-sonnet-4-6", safety_margin=0.8)
        capacity = strategy.capacity_chars()
        text = "\n\n".join(f"## Section {i}\n" + ("body text. " * 200) for i in range(20))
        assert len(text) > capacity  # genuinely oversized
        client = MockLLMClient([extraction("Login")] * 50)
        ChunkedRequirementAnalyzer(client, PromptLoader(), settings).run(
            RequirementInput(text=text, source_name="big.pdf")
        )
        assert client.call_count > 1  # chunked

    def test_fixed_strategy_honored_by_analyzer(self) -> None:
        settings = QAOpsSettings(
            provider="anthropic",
            model="claude-sonnet-4-6",
            chunking_strategy="fixed",
            chunk_size=500,
            chunk_overlap=100,
        )
        text = "\n\n".join(f"## Section {i}\n" + ("body. " * 30) for i in range(6))
        client = MockLLMClient([extraction("Login")] * 50)
        ChunkedRequirementAnalyzer(client, PromptLoader(), settings).run(
            RequirementInput(text=text, source_name="doc.md")
        )
        # Fixed 500-char chunks over a much longer document means many calls.
        assert client.call_count > 1
