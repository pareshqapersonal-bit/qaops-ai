"""OpenRouter provider-wide quota handling tests (ADR-034).

Two distinct behaviours must hold, verified without any live call:

7. An account-wide OpenRouter exhaustion ("free-models-per-day") disables
   OpenRouter for the rest of the run - it does NOT walk/retry more free models,
   because the daily cap is shared across all :free models.
8. A model-specific / transient 429 does NOT disable the whole provider; it
   stays bounded-retry then, if still failing, moves to the next model - the
   provider remains usable.
"""

from collections.abc import Sequence

from pydantic import BaseModel

from qaops.config import QAOpsSettings
from qaops.execution.executor import AdaptiveExecutor
from qaops.execution.models import ModelInfo, ModelRegistry
from qaops.execution.policy import Action, FailureKind, classify_failure, recovery_for
from qaops.execution.registry import ProviderInfo

# The exact account-wide message OpenRouter returns when the shared free daily
# cap is hit (verified from OpenRouter error output).
_ACCOUNT_WIDE = (
    "Rate limit exceeded: free-models-per-day. "
    "Add 10 credits to unlock 1000 free model requests per day"
)
# A per-model / transient rate limit.
_TRANSIENT = "HTTP 429 Too Many Requests: rate limited, slow down"


class Doc(BaseModel):
    trace: list[str] = []


class CountingStage:
    """Fails a bounded number of times with a given message, then succeeds."""

    def __init__(self, provider: str, model: str, message: str, fail_times: int) -> None:
        self._provider = provider
        self._model = model
        self._message = message
        self._fail_times = fail_times
        self.name = "stage"

    def run(self, data: Doc) -> Doc:
        key = f"{self._provider}/{self._model}"
        CALLS.append(key)
        if len([c for c in CALLS if c == key]) <= self._fail_times:
            raise RuntimeError(self._message)
        return Doc(trace=[*data.trace, key])


CALLS: list[str] = []


class _Registry(ModelRegistry):
    def __init__(self, models: dict[str, list[ModelInfo]]) -> None:
        super().__init__(discovery_enabled=False)
        self._models = models

    def models_for(self, provider: str) -> list[ModelInfo]:
        return list(self._models.get(provider, []))


class TestPolicyClassification:
    def test_account_wide_message_disables_provider(self) -> None:
        assert classify_failure(_ACCOUNT_WIDE) is FailureKind.PROVIDER_RATE_LIMIT
        recovery = recovery_for(_ACCOUNT_WIDE)
        assert recovery.action is Action.DISABLE_AND_SWITCH
        assert recovery.disables_provider is True

    def test_transient_message_is_bounded_retry(self) -> None:
        assert classify_failure(_TRANSIENT) is FailureKind.RATE_LIMIT
        recovery = recovery_for(_TRANSIENT)
        assert recovery.action is Action.RETRY_SAME_WITH_BACKOFF
        assert recovery.disables_provider is False


class TestAccountWideExhaustion:
    def test_openrouter_account_exhaustion_disables_provider_for_run(self) -> None:
        # OpenRouter has two free models; the FIRST call hits the account-wide
        # cap. The provider must be disabled without trying the second model,
        # and execution moves to the backup provider (groq).
        CALLS.clear()
        registry = _Registry(
            {
                "openrouter": [
                    ModelInfo(name="a:free", provider="openrouter", free=True, priority=10),
                    ModelInfo(name="b:free", provider="openrouter", free=True, priority=20),
                ],
                "groq": [ModelInfo(name="llama-3.3-70b-versatile", provider="groq", free=True)],
            }
        )

        def factory(settings: QAOpsSettings) -> Sequence[CountingStage]:
            provider = settings.provider
            model = settings.openrouter_model if provider == "openrouter" else settings.groq_model
            # OpenRouter always returns the account-wide error; groq succeeds.
            message = _ACCOUNT_WIDE if provider == "openrouter" else ""
            return [CountingStage(provider, model, message, fail_times=10 if message else 0)]

        providers = [
            ProviderInfo(name="openrouter", key_variables=("OPENROUTER_API_KEY",)),
            ProviderInfo(name="groq", key_variables=("GROQ_API_KEY",)),
        ]
        settings = QAOpsSettings(provider="openrouter", execution_strategy="free_first")
        executor = AdaptiveExecutor(
            providers, settings, factory, registry=registry, sleep=lambda _s: None
        )
        result = executor.run(Doc())

        # Recovered on groq.
        assert result.trace == ["groq/llama-3.3-70b-versatile"]
        # Only ONE OpenRouter model was ever attempted - the account-wide cap
        # disabled the provider instead of walking to the second free model.
        openrouter_models_tried = {c for c in CALLS if c.startswith("openrouter/")}
        assert len(openrouter_models_tried) == 1
        # The provider was recorded as unavailable with a clear reason.
        health = executor.report.health["openrouter"]
        assert health.available is False
        assert health.reason  # a non-empty disable reason for telemetry


class TestTransientDoesNotDisableProvider:
    def test_transient_429_stays_within_provider(self) -> None:
        # A single free OpenRouter model returns a transient 429 twice, then
        # succeeds. The provider must NOT be disabled; the same model is retried
        # with backoff and ultimately succeeds on OpenRouter itself.
        CALLS.clear()
        registry = _Registry(
            {
                "openrouter": [
                    ModelInfo(name="a:free", provider="openrouter", free=True, priority=10)
                ],
            }
        )

        def factory(settings: QAOpsSettings) -> Sequence[CountingStage]:
            return [
                CountingStage("openrouter", settings.openrouter_model, _TRANSIENT, fail_times=2)
            ]

        providers = [ProviderInfo(name="openrouter", key_variables=("OPENROUTER_API_KEY",))]
        settings = QAOpsSettings(provider="openrouter", execution_strategy="free_first")
        executor = AdaptiveExecutor(
            providers, settings, factory, registry=registry, sleep=lambda _s: None
        )
        result = executor.run(Doc())

        # Succeeded on OpenRouter after bounded retries - provider stayed usable.
        assert result.trace == ["openrouter/a:free"]
        assert executor.report.health["openrouter"].available is True
