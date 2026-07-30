"""Phase 20 provider-reliability regression tests (ADR-035).

Reproduces the meaningful parts of the production smoke-test incident without
any live LLM call: unsuitable-model rejection, Gemini 404 handling, Groq
rate-limit scope, bounded UNKNOWN recovery, structured attempt history, and
credential sanitization. Also an executor-level end-to-end exhaustion scenario
(OpenRouter unusable -> Groq exhausted -> Gemini unavailable -> clean failure)
that must terminate within the configured budgets.
"""

import contextlib
from collections.abc import Sequence

from pydantic import BaseModel

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.execution.executor import AdaptiveExecutor
from qaops.execution.models import (
    ModelInfo,
    ModelRegistry,
    _is_text_capable,
    discover_openrouter_models,
)
from qaops.execution.policy import (
    Action,
    FailureKind,
    classify_failure_fields,
    recovery_for_exception,
)
from qaops.execution.selector import StageRequirements, select_candidates
from qaops.llm.errors import LLMProviderError, extract_openai_error_fields


class Doc(BaseModel):
    trace: list[str] = []


# --- Requirement 2 & 10: unsuitable model rejection --------------------------


class TestUnsuitableModelRejection:
    def test_lyria_audio_model_is_not_text_capable(self) -> None:
        # OpenRouter architecture metadata: text in, audio out -> not text.
        arch = {"input_modalities": ["text"], "output_modalities": ["audio"]}
        assert _is_text_capable(arch) is False

    def test_image_output_model_is_not_text_capable(self) -> None:
        arch = {"input_modalities": ["text"], "output_modalities": ["image"]}
        assert _is_text_capable(arch) is False

    def test_text_model_is_text_capable(self) -> None:
        arch = {"input_modalities": ["text"], "output_modalities": ["text"]}
        assert _is_text_capable(arch) is True

    def test_missing_metadata_defaults_capable(self) -> None:
        # Conservative fallback: unknown modality does not silently exclude.
        assert _is_text_capable(None) is True

    def test_lyria_is_filtered_from_requirement_analyzer_candidates(self) -> None:
        # The exact production offenders must never be candidates for a stage.
        lyria_clip = ModelInfo(
            name="google/lyria-3-clip-preview",
            provider="openrouter",
            max_context_tokens=200_000,
            text_capable=False,
        )
        lyria_pro = ModelInfo(
            name="google/lyria-3-pro-preview",
            provider="openrouter",
            max_context_tokens=200_000,
            text_capable=False,
        )
        good = ModelInfo(
            name="meta-llama/llama-3.3-70b-instruct:free",
            provider="openrouter",
            free=True,
            text_capable=True,
        )
        chosen = select_candidates([lyria_clip, lyria_pro, good], StageRequirements(), limit=5)
        names = {c.model.name for c in chosen}
        assert "google/lyria-3-clip-preview" not in names
        assert "google/lyria-3-pro-preview" not in names
        assert "meta-llama/llama-3.3-70b-instruct:free" in names

    def test_huge_context_does_not_rescue_unsuitable_model(self) -> None:
        # A giant context window must not make a non-text model competitive:
        # eligibility is checked before ranking.
        big_but_unsuitable = ModelInfo(
            name="google/lyria-3-pro-preview",
            provider="openrouter",
            max_context_tokens=2_000_000,
            text_capable=False,
        )
        chosen = select_candidates([big_but_unsuitable], StageRequirements(), limit=5)
        assert chosen == []

    def test_discovery_marks_non_text_models(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        # A discovery payload containing a music model marks it text_capable=False.
        payload = {
            "data": [
                {
                    "id": "google/lyria-3-pro-preview",
                    "context_length": 8192,
                    "architecture": {"input_modalities": ["text"], "output_modalities": ["audio"]},
                    "pricing": {"prompt": "0"},
                },
                {
                    "id": "meta-llama/llama-3.3-70b-instruct:free",
                    "context_length": 128000,
                    "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                    "pricing": {"prompt": "0"},
                },
            ]
        }
        monkeypatch.setattr("qaops.execution.models._http_get_json", lambda _url: payload)
        models = {m.name: m for m in discover_openrouter_models()}
        assert models["google/lyria-3-pro-preview"].text_capable is False
        assert models["meta-llama/llama-3.3-70b-instruct:free"].text_capable is True


# --- Requirement 4: Gemini availability --------------------------------------


class TestGeminiAvailability:
    def test_exact_production_404_classifies_as_model_unavailable(self) -> None:
        message = (
            "This model models/gemini-2.5-flash is no longer available to new "
            "users. Please update your code to use a newer model. 404 NOT_FOUND"
        )
        exc = LLMProviderError("gemini", message, status_code=404)
        recovery = recovery_for_exception(exc)
        assert recovery.kind is FailureKind.MODEL_UNAVAILABLE
        assert recovery.disables_model is True

    def test_404_status_alone_classifies_as_model_unavailable(self) -> None:
        # Even if the message were opaque, the 404 status resolves it.
        assert classify_failure_fields("opaque", status_code=404) is FailureKind.MODEL_UNAVAILABLE


# --- Requirements 5 & 6: Groq rate-limit scope & classification --------------


class TestGroqRateLimitScope:
    def test_opaque_429_status_is_rate_limit_not_unknown(self) -> None:
        # The Phase 20 rate_limit->unknown fix: a 429 whose message lacks a
        # known substring is still a rate limit via the structured status.
        assert (
            classify_failure_fields("upstream connect error", status_code=429)
            is FailureKind.RATE_LIMIT
        )

    def test_transient_429_is_bounded_retry_and_keeps_provider(self) -> None:
        exc = LLMProviderError("groq", "429 rate limited", status_code=429)
        recovery = recovery_for_exception(exc)
        assert recovery.action is Action.RETRY_SAME_WITH_BACKOFF
        assert recovery.disables_provider is False

    def test_account_quota_code_disables_provider(self) -> None:
        exc = LLMProviderError("groq", "quota", status_code=429, error_code="insufficient_quota")
        recovery = recovery_for_exception(exc)
        assert recovery.kind is FailureKind.PROVIDER_RATE_LIMIT
        assert recovery.disables_provider is True

    def test_extract_fields_reads_status_and_code(self) -> None:
        class FakeStatusError(Exception):
            status_code = 429
            code = "rate_limit_exceeded"

        status, code = extract_openai_error_fields(FakeStatusError())
        assert status == 429
        assert code == "rate_limit_exceeded"

    def test_extract_fields_reads_nested_body_code(self) -> None:
        class FakeBodyError(Exception):
            status_code = 402
            body = {"error": {"code": "insufficient_quota"}}

        status, code = extract_openai_error_fields(FakeBodyError())
        assert status == 402
        assert code == "insufficient_quota"


# --- Executor-level scenarios ------------------------------------------------


class _ScriptedStage:
    """Raises a scripted exception for its provider, or succeeds."""

    def __init__(self, provider: str, model: str, script: dict[str, Exception]) -> None:
        self._provider = provider
        self._model = model
        self._script = script
        self.name = "requirement_analyzer"

    def run(self, data: Doc) -> Doc:
        exc = self._script.get(self._provider)
        if exc is not None:
            raise exc
        return Doc(trace=[*data.trace, f"{self._provider}/{self._model}"])


class _Registry(ModelRegistry):
    def __init__(self, models: dict[str, list[ModelInfo]]) -> None:
        super().__init__(discovery_enabled=False)
        self._models = models

    def models_for(self, provider: str) -> list[ModelInfo]:
        return list(self._models.get(provider, []))


def _incident_registry() -> _Registry:
    return _Registry(
        {
            "openrouter": [
                ModelInfo(name="a:free", provider="openrouter", free=True, text_capable=True),
                ModelInfo(name="b:free", provider="openrouter", free=True, text_capable=True),
            ],
            "groq": [
                ModelInfo(name="llama-3.3-70b-versatile", provider="groq", free=True),
                ModelInfo(name="openai/gpt-oss-120b", provider="groq", free=True),
                ModelInfo(name="llama-3.1-8b-instant", provider="groq", free=True),
            ],
            "gemini": [
                ModelInfo(name="gemini-flash-latest", provider="gemini", free=True),
            ],
        }
    )


_MODEL_FIELD = {
    "openrouter": "openrouter_model",
    "groq": "groq_model",
    "gemini": "gemini_model",
}


class TestExecutorExhaustionScenario:
    def _providers(self, *names: str):  # type: ignore[no-untyped-def]
        from qaops.execution.registry import ProviderInfo

        keys = {
            "openrouter": ("OPENROUTER_API_KEY",),
            "groq": ("GROQ_API_KEY",),
            "gemini": ("GEMINI_API_KEY",),
        }
        return [ProviderInfo(name=n, key_variables=keys[n]) for n in names]

    def test_full_incident_terminates_cleanly_with_history(self) -> None:
        # OpenRouter account-wide quota, Groq account quota, Gemini 404 ->
        # clean StageError carrying an ordered sanitized attempt history.
        script = {
            "openrouter": LLMProviderError(
                "openrouter",
                "Rate limit exceeded: free-models-per-day",
                status_code=429,
            ),
            "groq": LLMProviderError(
                "groq", "quota", status_code=429, error_code="insufficient_quota"
            ),
            "gemini": LLMProviderError(
                "gemini",
                "models/gemini-flash-latest is no longer available 404",
                status_code=404,
            ),
        }
        calls: list[str] = []

        def factory(settings: QAOpsSettings) -> Sequence[_ScriptedStage]:
            model = str(getattr(settings, _MODEL_FIELD[settings.provider]))
            calls.append(f"{settings.provider}/{model}")
            return [_ScriptedStage(settings.provider, model, script)]

        providers = self._providers("openrouter", "groq", "gemini")
        settings = QAOpsSettings(provider="openrouter", execution_strategy="free_first")
        executor = AdaptiveExecutor(
            providers, settings, factory, registry=_incident_registry(), sleep=lambda _s: None
        )

        raised: StageError | None = None
        try:
            executor.run(Doc())
        except StageError as exc:
            raised = exc

        assert raised is not None
        # Attempt history preserves provider/model/failure ordering.
        history = raised.attempts
        assert history, "expected a populated attempt history"
        providers_in_order = [h["provider"] for h in history]
        # OpenRouter first, then Groq, then Gemini (failover order preserved).
        assert providers_in_order.index("openrouter") < providers_in_order.index("groq")
        assert providers_in_order.index("groq") < providers_in_order.index("gemini")
        # Account-wide OpenRouter quota disabled it after ONE model (not both).
        openrouter_models = {h["model"] for h in history if h["provider"] == "openrouter"}
        assert len(openrouter_models) == 1
        # Gemini 404 recorded as model_unavailable.
        gemini_kinds = {h["failure_kind"] for h in history if h["provider"] == "gemini"}
        assert "model_unavailable" in gemini_kinds

    def test_scenario_stays_within_call_budget(self) -> None:
        # Everything fails transiently; run must terminate within the provider
        # call budget rather than walking unbounded.
        script = {
            "openrouter": LLMProviderError("openrouter", "boom", status_code=500),
            "groq": LLMProviderError("groq", "boom", status_code=500),
            "gemini": LLMProviderError("gemini", "boom", status_code=500),
        }
        total_calls = 0

        def factory(settings: QAOpsSettings) -> Sequence[_ScriptedStage]:
            nonlocal total_calls
            total_calls += 1
            model = str(getattr(settings, _MODEL_FIELD[settings.provider]))
            return [_ScriptedStage(settings.provider, model, script)]

        providers = self._providers("openrouter", "groq", "gemini")
        settings = QAOpsSettings(provider="openrouter", execution_strategy="free_first")
        executor = AdaptiveExecutor(
            providers, settings, factory, registry=_incident_registry(), sleep=lambda _s: None
        )
        with contextlib.suppress(StageError):
            executor.run(Doc())
        # Hard ceiling: 3 providers * per-provider budget, well bounded. The
        # exact number is not asserted, only that it did not run away.
        assert total_calls <= settings.max_provider_calls_per_stage * 3

    def test_unknown_failure_recovery_is_bounded(self) -> None:
        # A completely unrecognized error (no substring, no status) is UNKNOWN
        # and must recover boundedly, not loop forever.
        script = {
            "openrouter": LLMProviderError("openrouter", "??? weird ???"),
            "groq": LLMProviderError("groq", "??? weird ???"),
            "gemini": LLMProviderError("gemini", "??? weird ???"),
        }
        calls = 0

        def factory(settings: QAOpsSettings) -> Sequence[_ScriptedStage]:
            nonlocal calls
            calls += 1
            model = str(getattr(settings, _MODEL_FIELD[settings.provider]))
            return [_ScriptedStage(settings.provider, model, script)]

        providers = self._providers("openrouter", "groq", "gemini")
        settings = QAOpsSettings(provider="openrouter", execution_strategy="free_first")
        executor = AdaptiveExecutor(
            providers, settings, factory, registry=_incident_registry(), sleep=lambda _s: None
        )
        raised = False
        try:
            executor.run(Doc())
        except StageError:
            raised = True
        assert raised
        assert calls <= settings.max_provider_calls_per_stage * 3


# --- Requirement 3: FREE_ONLY still never invokes paid -----------------------


class TestFreeOnlyStillHolds:
    def test_free_only_never_reaches_paid_even_with_unsuitable_filtering(self) -> None:
        from qaops.execution.registry import ProviderInfo

        registry = _Registry(
            {
                "groq": [ModelInfo(name="llama-3.3-70b-versatile", provider="groq", free=True)],
                "anthropic": [
                    ModelInfo(name="claude-sonnet-4-6", provider="anthropic", free=False)
                ],
            }
        )
        providers = [
            ProviderInfo(name="anthropic", key_variables=("ANTHROPIC_API_KEY",)),
            ProviderInfo(name="groq", key_variables=("GROQ_API_KEY",)),
        ]
        settings = QAOpsSettings(provider="groq", execution_strategy="free_only")
        executor = AdaptiveExecutor(
            providers, settings, lambda _s: [], registry=registry, sleep=lambda _s: None
        )
        assert [p.name for p in executor._providers] == ["groq"]


# --- Requirement 11.10: no sensitive material in history ---------------------


class TestSanitization:
    def test_attempt_history_has_no_credentials_or_raw_bodies(self) -> None:
        script = {
            "groq": LLMProviderError(
                "groq",
                "Error 401 with Authorization: Bearer sk-secret-KEY and api_key=sk-leak",
                status_code=401,
            ),
        }

        def factory(settings: QAOpsSettings) -> Sequence[_ScriptedStage]:
            return [_ScriptedStage("groq", "llama-3.3-70b-versatile", script)]

        from qaops.execution.registry import ProviderInfo

        registry = _Registry(
            {"groq": [ModelInfo(name="llama-3.3-70b-versatile", provider="groq", free=True)]}
        )
        providers = [ProviderInfo(name="groq", key_variables=("GROQ_API_KEY",))]
        settings = QAOpsSettings(provider="groq")
        executor = AdaptiveExecutor(
            providers, settings, factory, registry=registry, sleep=lambda _s: None
        )
        raised: StageError | None = None
        try:
            executor.run(Doc())
        except StageError as exc:
            raised = exc
        assert raised is not None
        blob = repr(raised.attempts)
        # The sanitized history carries only normalized fields, never secrets.
        assert "sk-secret-KEY" not in blob
        assert "sk-leak" not in blob
        assert "Bearer" not in blob
        # But it does carry the normalized failure kind.
        assert any(h["failure_kind"] == "authentication" for h in raised.attempts)
