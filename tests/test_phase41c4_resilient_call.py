"""Phase 41C-4 tests: shared candidate builder + resilient structured call.

Covers the extracted build_candidate_models primitive (canonical free/image rules,
synthetic candidates) and the resilient_structured_call helper (provider failover,
retry-same, bounded exhaustion, image-only filtering, text-only downstream,
free_only, configured ordering, fresh client per attempt, budgets). The executor's
own behaviour is guarded by its existing suite; these tests target the new code.
"""

from unittest.mock import patch

import pytest

from qaops.config import QAOpsSettings
from qaops.execution.candidates import (
    build_candidate_models,
    configured_model_is_free,
    settings_for_model,
    synthetic_candidate,
)
from qaops.execution.models import ModelInfo
from qaops.execution.registry import get_provider
from qaops.execution.resilient_call import (
    ResilientCallError,
    resilient_structured_call,
)
from qaops.execution.selector import StageRequirements
from qaops.llm.errors import LLMProviderError, LLMResponseFormatError


class _Stub:
    """Minimal LLM client stub exposing what run_call/tests read."""

    def __init__(self, provider: str) -> None:
        self.provider_name = provider
        self.model = "stub-model"


_MODEL_FIELDS = ("groq_model", "nvidia_model", "gemini_model", "openrouter_model", "model")


def _model_stub(settings) -> _Stub:
    """A stub whose .model reflects the model injected by settings_for_model.

    Lets multi-model-provider tests distinguish which sibling was selected.
    """
    stub = _Stub(settings.provider)
    for field in _MODEL_FIELDS:
        value = getattr(settings, field, None)
        if value:
            stub.model = value
            break
    return stub


def _text(free_only: bool = True) -> StageRequirements:
    return StageRequirements(needs_structured_output=True, free_only=free_only)


def _two_free_candidates() -> list[ModelInfo]:
    # nvidia (image-capable) and groq (text-only), both free.
    return [
        ModelInfo(name="nemotron", provider="nvidia", free=True, images_supported=True),
        ModelInfo(name="llama", provider="groq", free=True, images_supported=False),
    ]


# -- build_candidate_models / canonical rules ---------------------------------


class TestCandidateBuilder:
    def test_gemini_free_only_for_flash(self) -> None:
        # Canonical rule preserved: Gemini free ONLY when the configured model is a
        # flash tier - the exact rule that my earlier constant got wrong.
        providers = [get_provider("gemini")]
        flash = QAOpsSettings(provider="gemini", gemini_model="gemini-2.0-flash")
        paid = QAOpsSettings(provider="gemini", gemini_model="gemini-2.0-pro")
        assert configured_model_is_free(flash, "gemini", providers) is True
        assert configured_model_is_free(paid, "gemini", providers) is False

    def test_nvidia_is_free(self) -> None:
        assert configured_model_is_free(
            QAOpsSettings(provider="nvidia"), "nvidia", [get_provider("nvidia")]
        )

    def test_anthropic_not_free(self) -> None:
        assert not configured_model_is_free(
            QAOpsSettings(provider="anthropic"), "anthropic", [get_provider("anthropic")]
        )

    def test_synthetic_candidate_carries_image_flag(self) -> None:
        providers = [get_provider("nvidia")]
        cand = synthetic_candidate(
            QAOpsSettings(provider="nvidia"), "nvidia", "nemotron", providers
        )
        assert cand.provider == "nvidia"
        assert cand.images_supported is True  # nvidia is image-capable

    def test_build_uses_synthetic_when_no_catalogue(self) -> None:
        # With no discovered models (keyless env), one synthetic candidate per
        # provider is produced.
        settings = QAOpsSettings(provider="nvidia")
        models = build_candidate_models(
            providers=[get_provider("nvidia")],
            settings=settings,
            registry=_EmptyRegistry(),
        )
        assert len(models) == 1
        assert models[0].provider == "nvidia"

    def test_settings_for_model_injects_provider_and_model(self) -> None:
        settings = QAOpsSettings(provider="nvidia")
        model = ModelInfo(name="llama", provider="groq", free=True)
        updated = settings_for_model(settings, model)
        assert updated.provider == "groq"
        assert updated.groq_model == "llama"


class _EmptyRegistry:
    def models_for(self, _provider: str) -> list[ModelInfo]:
        return []


# -- resilient_structured_call ------------------------------------------------


def _run(settings, requirements, run_call, candidates, **kw):
    with patch(
        "qaops.execution.resilient_call.create_client",
        side_effect=lambda s: _Stub(s.provider),
    ):
        return resilient_structured_call(
            settings=settings,
            requirements=requirements,
            run_call=run_call,
            candidates=candidates,
            sleep=lambda _s: None,
            **kw,
        )


class TestResilience:
    def test_next_model_failover_off_nvidia_500(self) -> None:
        # A NVIDIA 500 (NEXT_MODEL) fails over to the next eligible provider.
        settings = QAOpsSettings(provider="nvidia")
        # nvidia scored below groq for text, so put nvidia first artificially by
        # making only nvidia present then groq: use an image req so nvidia is picked
        # first, but that filters groq out. Instead assert on a text call where the
        # top pick fails and the next succeeds.
        tried: list[str] = []

        def run_call(client):
            tried.append(client.provider_name)
            if client.provider_name == "groq":
                raise LLMProviderError("groq", "Error code: 500 EngineCore")
            return f"ok:{client.provider_name}"

        result = _run(settings, _text(), run_call, _two_free_candidates())
        assert result == "ok:nvidia"
        assert tried == ["groq", "nvidia"]  # failed over to the next candidate

    def test_retry_same_on_rate_limit(self) -> None:
        # A 429 classifies RETRY_SAME_WITH_BACKOFF -> retry the SAME candidate.
        settings = QAOpsSettings(provider="groq")
        tried: list[str] = []
        calls = {"n": 0}

        def run_call(client):
            tried.append(client.provider_name)
            calls["n"] += 1
            if calls["n"] == 1:
                raise LLMProviderError("groq", "Error code: 429 rate limit exceeded")
            return "ok"

        result = _run(
            settings, _text(), run_call, [ModelInfo(name="llama", provider="groq", free=True)]
        )
        assert result == "ok"
        assert tried == ["groq", "groq"]  # same candidate retried, not failed over

    def test_bounded_exhaustion(self) -> None:
        # Every candidate 500s -> bounded ResilientCallError, no infinite loop.
        settings = QAOpsSettings(provider="nvidia")
        tried: list[str] = []

        def run_call(client):
            tried.append(client.provider_name)
            raise LLMProviderError(client.provider_name, "Error code: 500 EngineCore")

        with pytest.raises(ResilientCallError):
            _run(settings, _text(), run_call, _two_free_candidates())
        # Each candidate tried once (NEXT_MODEL excludes then advances), then stops.
        assert sorted(tried) == ["groq", "nvidia"]

    def test_format_error_fails_over_then_exhausts(self) -> None:
        # A schema/format error classifies as next_model (not terminal): the helper
        # fails over, and with a single candidate that means bounded exhaustion.
        # This documents that no common clarification exception is "raise-now".
        settings = QAOpsSettings(provider="groq")
        tried: list[str] = []

        def run_call(client):
            tried.append(client.provider_name)
            raise LLMResponseFormatError("ShapedQuestionBatch", 3, ["bad json"])

        with pytest.raises(ResilientCallError):
            _run(
                settings,
                _text(),
                run_call,
                [ModelInfo(name="llama", provider="groq", free=True)],
            )
        assert tried == ["groq"]  # tried once, excluded (next_model), then exhausted

    def test_disable_and_switch_advances_to_next_provider(self) -> None:
        # An auth failure (DISABLE_AND_SWITCH) on the first candidate advances to
        # the next provider's candidate rather than raising.
        settings = QAOpsSettings(provider="groq")
        tried: list[str] = []

        def run_call(client):
            tried.append(client.provider_name)
            if client.provider_name == "groq":
                raise LLMProviderError("groq", "Error code: 401 invalid api key")
            return "ok"

        result = _run(settings, _text(), run_call, _two_free_candidates())
        assert result == "ok"
        assert tried == ["groq", "nvidia"]  # switched off groq to the next candidate

    def test_disable_and_switch_skips_sibling_models_same_provider(self) -> None:
        # Corrected semantics (ADR-063): DISABLE_AND_SWITCH disables the WHOLE
        # provider, so a sibling model on the same provider is NOT tried - matching
        # the executor's report.health[provider] disabling, not mere model exclusion.
        settings = QAOpsSettings(provider="groq")
        cands = [
            ModelInfo(name="groq-a", provider="groq", free=True),
            ModelInfo(name="groq-b", provider="groq", free=True),  # sibling
            ModelInfo(name="nv", provider="nvidia", free=True, images_supported=True),
        ]
        tried: list[tuple[str, str]] = []

        def run_call(client):
            tried.append((client.provider_name, client.model))
            if client.provider_name == "groq":
                raise LLMProviderError("groq", "Error code: 401 invalid api key")
            return "ok"

        with patch(
            "qaops.execution.resilient_call.create_client",
            side_effect=lambda s: _model_stub(s),
        ):
            result = resilient_structured_call(
                settings=settings,
                requirements=_text(),
                run_call=run_call,
                candidates=cands,
                sleep=lambda _s: None,
            )
        assert result == "ok"
        groq_tries = [t for t in tried if t[0] == "groq"]
        assert len(groq_tries) == 1  # provider disabled after the first failure
        assert tried[-1][0] == "nvidia"  # failed over to the other provider

    def test_drop_model_allows_sibling_models_same_provider(self) -> None:
        # DROP_MODEL_AND_CONTINUE (model_unavailable) excludes only the failing
        # model; a sibling on the SAME provider is still tried (provider stays up).
        settings = QAOpsSettings(provider="groq")
        cands = [
            ModelInfo(name="groq-a", provider="groq", free=True),
            ModelInfo(name="groq-b", provider="groq", free=True),  # sibling
        ]
        tried: list[tuple[str, str]] = []

        def run_call(client):
            tried.append((client.provider_name, client.model))
            if client.model == "groq-a":
                raise LLMProviderError("groq", "Error code: 404 model not found")
            return "ok"

        with patch(
            "qaops.execution.resilient_call.create_client",
            side_effect=lambda s: _model_stub(s),
        ):
            result = resilient_structured_call(
                settings=settings,
                requirements=_text(),
                run_call=run_call,
                candidates=cands,
                sleep=lambda _s: None,
            )
        assert result == "ok"
        groq_tries = [t for t in tried if t[0] == "groq"]
        assert len(groq_tries) == 2  # sibling tried; provider NOT disabled
        assert tried == [("groq", "groq-a"), ("groq", "groq-b")]

    def test_next_model_excludes_only_failing_model(self) -> None:
        # NEXT_MODEL (e.g. UNKNOWN / invalid_output) is model-level: a sibling on
        # the same provider remains eligible (provider not disabled).
        settings = QAOpsSettings(provider="groq")
        cands = [
            ModelInfo(name="groq-a", provider="groq", free=True),
            ModelInfo(name="groq-b", provider="groq", free=True),
        ]
        tried: list[tuple[str, str]] = []

        def run_call(client):
            tried.append((client.provider_name, client.model))
            if client.model == "groq-a":
                raise LLMProviderError("groq", "some unrecognised failure")  # UNKNOWN
            return "ok"

        with patch(
            "qaops.execution.resilient_call.create_client",
            side_effect=lambda s: _model_stub(s),
        ):
            result = resilient_structured_call(
                settings=settings,
                requirements=_text(),
                run_call=run_call,
                candidates=cands,
                sleep=lambda _s: None,
            )
        assert result == "ok"
        assert tried == [("groq", "groq-a"), ("groq", "groq-b")]  # sibling allowed

    def test_image_call_filters_to_image_capable_only(self) -> None:
        # An image requirement selects only image-capable candidates (nvidia).
        settings = QAOpsSettings(provider="nvidia")
        tried: list[str] = []

        def run_call(client):
            tried.append(client.provider_name)
            return "ok"

        _run(
            settings,
            StageRequirements(needs_structured_output=True, free_only=True, needs_images=True),
            run_call,
            _two_free_candidates(),
        )
        assert tried == ["nvidia"]  # groq (text-only) filtered out

    def test_downstream_text_excludes_image_provider(self) -> None:
        # exclude_image_providers (Phase 40B downstream) drops nvidia for a text call.
        settings = QAOpsSettings(provider="nvidia")
        tried: list[str] = []

        def run_call(client):
            tried.append(client.provider_name)
            return "ok"

        _run(
            settings,
            StageRequirements(
                needs_structured_output=True, free_only=True, exclude_image_providers=True
            ),
            run_call,
            _two_free_candidates(),
        )
        assert tried == ["groq"]  # nvidia (image-capable) excluded downstream

    def test_free_only_excludes_paid(self) -> None:
        # Under free_only, a paid candidate is never tried.
        settings = QAOpsSettings(provider="anthropic")
        cands = [
            ModelInfo(name="claude", provider="anthropic", free=False),
            ModelInfo(name="llama", provider="groq", free=True),
        ]
        tried: list[str] = []

        def run_call(client):
            tried.append(client.provider_name)
            return "ok"

        _run(settings, _text(free_only=True), run_call, cands)
        assert tried == ["groq"]  # paid anthropic filtered by free_only

    def test_fresh_client_per_attempt(self) -> None:
        # Every attempt builds a fresh client (preserves the 41C-3 invariant).
        settings = QAOpsSettings(provider="nvidia")
        built: list[int] = []

        def run_call(client):
            built.append(id(client))
            if client.provider_name == "groq":
                raise LLMProviderError("groq", "Error code: 500 EngineCore")
            return "ok"

        with patch(
            "qaops.execution.resilient_call.create_client",
            side_effect=lambda s: _Stub(s.provider),
        ):
            resilient_structured_call(
                settings=settings,
                requirements=_text(),
                run_call=run_call,
                candidates=_two_free_candidates(),
                sleep=lambda _s: None,
            )
        # Two attempts (groq failover -> nvidia), two DISTINCT client objects.
        assert len(built) == 2
        assert len(set(built)) == 2

    def test_budget_bounds_retry_same(self) -> None:
        # A persistently rate-limited candidate is retried at most
        # max_attempts_per_model times, then fails over / exhausts - never forever.
        settings = QAOpsSettings(provider="groq")
        tried: list[str] = []

        def run_call(client):
            tried.append(client.provider_name)
            raise LLMProviderError(client.provider_name, "Error code: 429 rate limit")

        with pytest.raises(ResilientCallError):
            _run(
                settings,
                _text(),
                run_call,
                [ModelInfo(name="llama", provider="groq", free=True)],
                max_attempts_per_model=3,
            )
        assert tried == ["groq", "groq", "groq"]  # bounded at 3, then stop
