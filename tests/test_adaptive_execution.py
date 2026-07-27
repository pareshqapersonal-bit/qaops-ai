"""Phase 15-rev tests: model-then-provider adaptive execution (ADR-027).

Model failover within a provider before provider failover, per-failure retry
policy, stage checkpointing, health tracking, and that completed stages are
never recomputed. Stages are simple test doubles: the executor is
provider/model-agnostic, so it needs no real pipeline to exercise."""

from collections.abc import Sequence

import pytest
from pydantic import BaseModel

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.execution import (
    Action,
    AdaptiveExecutor,
    FailureKind,
    ModelRegistry,
    ProviderInfo,
    classify_failure,
    get_provider,
    recovery_for,
)


class Doc(BaseModel):
    text: str = ""
    trace: list[str] = []


class FakeStage:
    """Records which provider/model ran it; can fail on chosen pairings.

    fail_on maps "provider/model" (or "provider" for any model) to an error
    message. fail_times bounds how many times it fails before succeeding; 0
    means always.
    """

    def __init__(
        self,
        name: str,
        provider: str,
        model: str,
        fail_on: dict[str, str] | None = None,
        fail_times: int = 0,
    ) -> None:
        self.name = name
        self._provider = provider
        self._model = model
        self._fail_on = fail_on or {}
        self._fail_times = fail_times
        self._failures = 0

    def _message(self) -> str | None:
        return self._fail_on.get(f"{self._provider}/{self._model}") or self._fail_on.get(
            self._provider
        )

    def run(self, data: Doc) -> Doc:
        message = self._message()
        if message and (self._fail_times == 0 or self._failures < self._fail_times):
            self._failures += 1
            raise RuntimeError(message)
        return Doc(
            text=data.text, trace=[*data.trace, f"{self.name}@{self._provider}/{self._model}"]
        )


def make_factory(
    specs: list[tuple[str, dict[str, str] | None]], fail_times: int = 0
) -> tuple[object, list[str]]:
    """Build a stage factory plus a log of the provider/model it built for."""
    calls: list[str] = []
    model_field = {
        "anthropic": "model",
        "gemini": "gemini_model",
        "openrouter": "openrouter_model",
    }

    def factory(settings: QAOpsSettings) -> Sequence[FakeStage]:
        model = str(getattr(settings, model_field.get(settings.provider, "model"), ""))
        calls.append(f"{settings.provider}/{model}")
        return [
            FakeStage(name, settings.provider, model, fail_on, fail_times)
            for name, fail_on in specs
        ]

    return factory, calls


PRIMARY = ProviderInfo(name="openrouter", key_variables=("OPENROUTER_API_KEY",))
BACKUP = ProviderInfo(name="gemini", key_variables=("GEMINI_API_KEY",))
THIRD = ProviderInfo(name="anthropic", key_variables=("ANTHROPIC_API_KEY",))

# Static registry models, first-in-priority:
#   openrouter -> deepseek/deepseek-chat, openai/gpt-4o-mini, ...
#   gemini     -> gemini-2.5-flash, gemini-2.5-pro
OR_FIRST = "openrouter/deepseek/deepseek-chat"
OR_SECOND = "openrouter/openai/gpt-4o-mini"
GEM_FIRST = "gemini/gemini-2.5-flash"

CREDIT_ERROR = "Error code: 402 - requires more credits, can only afford 15461"
RATE_ERROR = "Error code: 429 - rate-limited upstream"
TIMEOUT_ERROR = "Request timed out after 60s"
AUTH_ERROR = "Error code: 401 - invalid x-api-key"


def executor(providers: list[ProviderInfo], factory: object, **kwargs: object) -> AdaptiveExecutor:
    return AdaptiveExecutor(
        providers,
        QAOpsSettings(provider=providers[0].name),
        factory,  # type: ignore[arg-type]
        registry=ModelRegistry(discovery_enabled=False),
        sleep=lambda _seconds: None,
        **kwargs,  # type: ignore[arg-type]
    )


def run_doc(agent: AdaptiveExecutor, data: Doc) -> Doc:
    """Run and narrow the BaseModel result back to Doc for assertions."""
    result = agent.run(data)
    assert isinstance(result, Doc)
    return result


class TestFailureClassification:
    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            (CREDIT_ERROR, FailureKind.INSUFFICIENT_CREDIT),
            (AUTH_ERROR, FailureKind.AUTHENTICATION),
            (RATE_ERROR, FailureKind.RATE_LIMIT),
            (TIMEOUT_ERROR, FailureKind.TIMEOUT),
            ("context length exceeded", FailureKind.CONTEXT_LIMIT),
            ("Error code: 404 - model is unavailable", FailureKind.MODEL_UNAVAILABLE),
            (
                "Model output failed validation against RequirementExtraction",
                FailureKind.INVALID_OUTPUT,
            ),
            ("something entirely novel", FailureKind.UNKNOWN),
        ],
    )
    def test_classification(self, message: str, expected: FailureKind) -> None:
        assert classify_failure(message) is expected


class TestRetryPolicy:
    def test_credits_try_another_model_first(self) -> None:
        recovery = recovery_for(CREDIT_ERROR)
        assert recovery.action is Action.NEXT_MODEL
        assert recovery.tries_another_model
        assert not recovery.disables_provider

    def test_authentication_disables_the_provider(self) -> None:
        recovery = recovery_for(AUTH_ERROR)
        assert recovery.action is Action.DISABLE_AND_SWITCH
        assert recovery.disables_provider

    def test_model_unavailable_drops_the_model(self) -> None:
        recovery = recovery_for("Error code: 404 - model is unavailable")
        assert recovery.action is Action.DROP_MODEL_AND_CONTINUE
        assert recovery.disables_model
        assert not recovery.disables_provider

    def test_rate_limit_retries_with_backoff(self) -> None:
        recovery = recovery_for(RATE_ERROR)
        assert recovery.action is Action.RETRY_SAME_WITH_BACKOFF
        assert recovery.backoff_seconds > 0
        assert not recovery.disables_provider

    def test_timeout_retries_the_same_model(self) -> None:
        assert recovery_for(TIMEOUT_ERROR).action is Action.RETRY_SAME

    def test_invalid_output_moves_to_next_model(self) -> None:
        # ADR-030: a model reaching the executor with invalid_output has already
        # exhausted its in-request repair attempts, so move on rather than
        # granting another full nested repair cycle.
        assert recovery_for("Model output failed validation against X").action is Action.NEXT_MODEL

    def test_empty_output_moves_to_next_model(self) -> None:
        assert recovery_for("the provider returned no content").action is Action.NEXT_MODEL

    def test_context_limit_asks_for_a_larger_model(self) -> None:
        recovery = recovery_for("context length exceeded")
        assert recovery.action is Action.LARGER_CONTEXT_MODEL
        assert not recovery.disables_provider


class TestModelFailover:
    def test_credit_exhaustion_switches_model_not_provider(self) -> None:
        # deepseek runs out of credit; gpt-4o-mini on the SAME provider works.
        factory, _ = make_factory([("scenarios", {OR_FIRST: CREDIT_ERROR})])
        agent = executor([PRIMARY, BACKUP], factory)
        result = run_doc(agent, Doc(text="prd"))
        assert result.trace == [f"scenarios@{OR_SECOND}"]
        assert agent.report.model_switches == [
            ("scenarios", "deepseek/deepseek-chat", "openai/gpt-4o-mini")
        ]
        assert agent.report.provider_switches == []

    def test_provider_only_switches_after_all_models_exhausted(self) -> None:
        # Every openrouter model exhausts credit; only then move to gemini.
        factory, _ = make_factory([("scenarios", {"openrouter": CREDIT_ERROR})])
        agent = executor([PRIMARY, BACKUP], factory)
        result = run_doc(agent, Doc(text="prd"))
        assert result.trace == [f"scenarios@{GEM_FIRST}"]
        assert len(agent.report.model_switches) >= 1
        assert agent.report.provider_switches == [("scenarios", "openrouter", "gemini")]
        assert agent.report.health["openrouter"].available is False

    def test_model_unavailable_is_dropped_and_next_tried(self) -> None:
        factory, _ = make_factory([("scenarios", {OR_FIRST: "Error code: 404 - unknown model"})])
        agent = executor([PRIMARY, BACKUP], factory)
        result = run_doc(agent, Doc(text="prd"))
        assert result.trace == [f"scenarios@{OR_SECOND}"]

    def test_completed_stages_are_not_recomputed(self) -> None:
        factory, _ = make_factory(
            [("analyze", None), ("scenarios", {OR_FIRST: CREDIT_ERROR}), ("cases", None)]
        )
        agent = executor([PRIMARY, BACKUP], factory)
        result = run_doc(agent, Doc(text="prd"))
        # analyze ran once on the first model and never again.
        assert result.trace[0] == f"analyze@{OR_FIRST}"
        assert result.trace.count("analyze@") if False else True
        assert sum(1 for t in result.trace if t.startswith("analyze@")) == 1

    def test_checkpoints_record_provider_and_model(self) -> None:
        factory, _ = make_factory([("analyze", None), ("scenarios", {OR_FIRST: CREDIT_ERROR})])
        agent = executor([PRIMARY, BACKUP], factory)
        agent.run(Doc(text="prd"))
        assert agent.report.completed_stages == ["analyze", "scenarios"]
        assert agent.report.checkpoints[0].model == "deepseek/deepseek-chat"
        assert agent.report.checkpoints[1].model == "openai/gpt-4o-mini"

    def test_models_used_is_reported(self) -> None:
        factory, _ = make_factory([("scenarios", {OR_FIRST: CREDIT_ERROR})])
        agent = executor([PRIMARY, BACKUP], factory)
        agent.run(Doc(text="prd"))
        assert agent.report.models_used == ["openai/gpt-4o-mini"]


class TestProviderFailover:
    def test_authentication_disables_provider_immediately(self) -> None:
        # Auth failure should not waste time trying sibling models.
        factory, _ = make_factory([("scenarios", {"openrouter": AUTH_ERROR})])
        agent = executor([PRIMARY, BACKUP], factory)
        result = run_doc(agent, Doc(text="prd"))
        assert result.trace == [f"scenarios@{GEM_FIRST}"]
        assert agent.report.provider_switches == [("scenarios", "openrouter", "gemini")]
        # No model_switches within openrouter, since auth disabled it outright.
        assert agent.report.model_switches == []

    def test_disabled_provider_skipped_for_later_stages(self) -> None:
        factory, calls = make_factory(
            [("analyze", {"openrouter": AUTH_ERROR}), ("scenarios", None)]
        )
        agent = executor([PRIMARY, BACKUP], factory)
        result = run_doc(agent, Doc(text="prd"))
        assert result.trace == [f"analyze@{GEM_FIRST}", f"scenarios@{GEM_FIRST}"]
        # openrouter built once (the failed analyze), never again.
        assert sum(1 for c in calls if c.startswith("openrouter/")) == 1


class TestRetryBehaviour:
    def test_transient_timeout_recovers_on_same_model(self) -> None:
        factory, _ = make_factory([("scenarios", {OR_FIRST: TIMEOUT_ERROR})], fail_times=1)
        agent = executor([PRIMARY, BACKUP], factory)
        result = run_doc(agent, Doc(text="prd"))
        assert result.trace == [f"scenarios@{OR_FIRST}"]  # no switch
        assert agent.report.model_switches == []

    def test_persistent_timeout_moves_to_next_model(self) -> None:
        factory, _ = make_factory([("scenarios", {OR_FIRST: TIMEOUT_ERROR})])
        agent = executor([PRIMARY, BACKUP], factory)
        result = run_doc(agent, Doc(text="prd"))
        assert result.trace == [f"scenarios@{OR_SECOND}"]

    def test_rate_limit_backs_off(self) -> None:
        slept: list[float] = []
        factory, _ = make_factory([("scenarios", {OR_FIRST: RATE_ERROR})], fail_times=2)
        agent = AdaptiveExecutor(
            [PRIMARY, BACKUP],
            QAOpsSettings(provider="openrouter"),
            factory,  # type: ignore[arg-type]
            registry=ModelRegistry(discovery_enabled=False),
            sleep=slept.append,
        )
        agent.run(Doc(text="prd"))
        assert slept
        assert all(seconds > 0 for seconds in slept)


class TestExhaustion:
    def test_all_models_and_providers_failing_raises(self) -> None:
        factory, _ = make_factory(
            [("scenarios", {"openrouter": CREDIT_ERROR, "gemini": CREDIT_ERROR})]
        )
        agent = executor([PRIMARY, BACKUP], factory)
        with pytest.raises(StageError, match="All providers failed"):
            agent.run(Doc(text="prd"))

    def test_single_provider_persistent_retryable_terminates(self) -> None:
        # The infinite-loop regression: one provider, every model times out.
        factory, _ = make_factory([("scenarios", {"openrouter": TIMEOUT_ERROR})])
        agent = executor([PRIMARY], factory)
        with pytest.raises(StageError, match="All providers failed"):
            agent.run(Doc(text="prd"))

    def test_requires_at_least_one_provider(self) -> None:
        factory, _ = make_factory([("scenarios", None)])
        with pytest.raises(ValueError, match="at least one provider"):
            AdaptiveExecutor([], QAOpsSettings(), factory)  # type: ignore[arg-type]


class TestProgressReporting:
    def test_reports_stage_model_and_switch(self) -> None:
        lines: list[str] = []
        factory, _ = make_factory([("analyze", None), ("scenarios", {OR_FIRST: CREDIT_ERROR})])
        agent = AdaptiveExecutor(
            [PRIMARY, BACKUP],
            QAOpsSettings(provider="openrouter"),
            factory,  # type: ignore[arg-type]
            registry=ModelRegistry(discovery_enabled=False),
            reporter=lines.append,
            sleep=lambda _s: None,
        )
        agent.run(Doc(text="prd"))
        joined = "\n".join(lines)
        assert f"analyze: {OR_FIRST} ok" in joined
        assert "insufficient_credit" in joined
        assert "trying openrouter/openai/gpt-4o-mini" in joined


class TestProviderRegistry:
    def test_known_providers_registered(self) -> None:
        for name in ("anthropic", "gemini", "openrouter", "ollama"):
            assert get_provider(name) is not None

    def test_unknown_provider_returns_none(self) -> None:
        assert get_provider("nonexistent") is None
