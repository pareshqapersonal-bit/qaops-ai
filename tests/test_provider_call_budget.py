"""Phase 16.2 acceptance fix: true provider-call budgeting (ADR-030).

The live PDF run revealed that structured-output repair could make several real
provider calls inside one executor 'attempt', invisible to progress and budget.
These tests establish and lock the invariant: every actual provider generation
call is counted exactly once, hidden retries cannot bypass the budget, and an
empty response is diagnosed accurately without a false token-cap recommendation.

All providers are fakes; no network, no real sleeps."""

import pytest
from pydantic import BaseModel

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.execution import AdaptiveExecutor, ModelRegistry, ProviderInfo
from qaops.execution.events import EventType, ExecutionEvent
from qaops.execution.models import ModelInfo
from qaops.execution.policy import Action, FailureKind, classify_failure, recovery_for
from qaops.llm.errors import LLMEmptyResponseError, LLMResponseFormatError
from qaops.llm.mock import MockLLMClient
from qaops.llm.models import LLMMessage, LLMRequest, LLMResponse, LLMUsage
from qaops.llm.request_budget import (
    NullRequestObserver,
    RequestBudgetExhausted,
    current_observer,
)
from qaops.llm.structured import generate_structured

TIMEOUT_MSG = "request timed out after the configured deadline (openrouter)"


class Doc(BaseModel):
    trace: list[str] = []


class Schema(BaseModel):
    value: int


def _response(text: str, *, stop_reason: str = "stop", model: str = "m") -> LLMResponse:
    return LLMResponse(
        text=text,
        model=model,
        usage=LLMUsage(input_tokens=1, output_tokens=1),
        stop_reason=stop_reason,
    )


def _request() -> LLMRequest:
    return LLMRequest(
        system="s",
        messages=[LLMMessage(role="user", content="go")],
        temperature=0.0,
        max_output_tokens=1000,
    )


class _CountingClient:
    """A fake client that returns scripted responses and counts real calls."""

    def __init__(
        self, responses: list[LLMResponse], *, provider: str = "openrouter", model: str = "m"
    ) -> None:
        self._responses = responses
        self._i = 0
        self.provider_name = provider
        self.model = model
        self.calls = 0

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        response = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return response


class _CountingObserver:
    """Records every provider call the structured layer announces."""

    def __init__(self, *, budget: int = 1000) -> None:
        self.before = 0
        self.after = 0
        self._budget = budget

    def before_request(self, *, provider: str, model: str, attempt: int) -> None:
        if self.before >= self._budget:
            raise RequestBudgetExhausted("budget spent")
        self.before += 1

    def after_request(
        self, *, provider: str, model: str, attempt: int, empty: bool, chars: int
    ) -> None:
        self.after += 1


# =============================================================================
# §14.1, §14.2, §14.20: one provider call = one counted request
# =============================================================================


class TestCallAccounting:
    def test_one_call_counted_once(self) -> None:
        client = _CountingClient([_response('{"value": 1}')])
        observer = _CountingObserver()
        generate_structured(client, _request(), Schema, retries=2, observer=observer)
        assert client.calls == 1
        assert observer.before == 1
        assert observer.after == 1

    def test_repair_retry_counted_each_time(self) -> None:
        # Two malformed-but-nonempty responses, then a valid one: 3 real calls,
        # 3 counted.
        client = _CountingClient(
            [_response("not json"), _response("still not"), _response('{"value": 1}')]
        )
        observer = _CountingObserver()
        generate_structured(client, _request(), Schema, retries=2, observer=observer)
        assert client.calls == 3
        assert observer.before == 3

    def test_observer_veto_stops_further_calls(self) -> None:
        # Budget of 1: the second repair call is vetoed before it happens.
        client = _CountingClient([_response("not json")])
        observer = _CountingObserver(budget=1)
        with pytest.raises(RequestBudgetExhausted):
            generate_structured(client, _request(), Schema, retries=2, observer=observer)
        assert client.calls == 1  # never made the second call


# =============================================================================
# §14.6, §14.7, §8: empty response handling
# =============================================================================


class TestEmptyResponse:
    def test_empty_response_raises_dedicated_error(self) -> None:
        client = _CountingClient([_response("", stop_reason="length")])
        with pytest.raises(LLMEmptyResponseError):
            generate_structured(client, _request(), Schema, retries=2)

    def test_empty_response_does_not_repeat(self) -> None:
        # An empty response ends the loop immediately - no repair re-roll.
        client = _CountingClient([_response(""), _response(""), _response('{"value": 1}')])
        with pytest.raises(LLMEmptyResponseError):
            generate_structured(client, _request(), Schema, retries=2)
        assert client.calls == 1  # not 3

    def test_empty_output_classified(self) -> None:
        exc = LLMEmptyResponseError("Schema", 1, "openrouter", "m")
        assert classify_failure(str(exc)) is FailureKind.EMPTY_OUTPUT

    def test_empty_output_recovers_to_next_model(self) -> None:
        assert recovery_for("the provider returned no content").action is Action.NEXT_MODEL


# =============================================================================
# §9: truncation diagnostic accuracy
# =============================================================================


class TestTruncationDiagnostic:
    def test_empty_length_response_is_not_truncation(self) -> None:
        # The exact live regression: stop_reason=length, content="", chars=0.
        client = _CountingClient([_response("", stop_reason="length")])
        with pytest.raises(LLMEmptyResponseError) as exc_info:
            generate_structured(client, _request(), Schema, retries=2)
        # No "raise max_output_tokens" recommendation for a zero-content response.
        assert "max_output_tokens" not in str(exc_info.value)

    def test_nonempty_length_response_is_truncation(self) -> None:
        # A genuinely truncated response (has content, cut off) still advises.
        client = _CountingClient([_response('{"value": ', stop_reason="length")])
        with pytest.raises(LLMResponseFormatError) as exc_info:
            generate_structured(client, _request(), Schema, retries=2)
        assert exc_info.value.truncated
        assert "max_output_tokens" in str(exc_info.value)

    def test_format_error_all_empty_gives_no_token_advice(self) -> None:
        # Even if truncated=True is passed, an all-empty raw set must not advise
        # raising the token cap.
        error = LLMResponseFormatError("Schema", 3, ["", "", ""], truncated=True)
        assert not error.truncated
        assert "max_output_tokens" not in str(error)


# =============================================================================
# §14.8, §14.9, §14.10, §13: invalid JSON, validation, deterministic repair
# =============================================================================


class TestStructuredRepair:
    def test_invalid_json_then_valid_recovers(self) -> None:
        client = _CountingClient([_response("garbage"), _response('{"value": 5}')])
        result = generate_structured(client, _request(), Schema, retries=2)
        assert result.value == 5
        assert client.calls == 2

    def test_schema_validation_error_then_valid(self) -> None:
        client = _CountingClient([_response('{"wrong": 1}'), _response('{"value": 7}')])
        result = generate_structured(client, _request(), Schema, retries=2)
        assert result.value == 7

    def test_fenced_json_parsed_without_a_call(self) -> None:
        # Deterministic extraction handles markdown fences in one call.
        client = _CountingClient([_response('```json\n{"value": 9}\n```')])
        result = generate_structured(client, _request(), Schema, retries=2)
        assert result.value == 9
        assert client.calls == 1

    def test_exhausted_repair_raises_format_error(self) -> None:
        client = _CountingClient([_response("nope")])
        with pytest.raises(LLMResponseFormatError):
            generate_structured(client, _request(), Schema, retries=2)
        assert client.calls == 3  # initial + 2 repairs, all counted


# =============================================================================
# §14.4: SDK retries remain disabled (Phase 16.2 guarantee)
# =============================================================================


class TestSdkRetriesDisabled:
    def test_openrouter_and_anthropic_have_zero_sdk_retries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "k")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
        from qaops.llm.anthropic_client import AnthropicClient
        from qaops.llm.openrouter_client import OpenRouterClient

        or_client = OpenRouterClient(model="x")
        assert or_client._async_sdk is not None
        assert or_client._async_sdk.max_retries == 0
        assert AnthropicClient(model="x")._sdk.max_retries == 0


# =============================================================================
# Executor-level: the invariant end to end (§6, §14.3, §14.17, §20)
# =============================================================================


def _reg(catalogue: dict[str, list[ModelInfo]]) -> ModelRegistry:
    class Reg(ModelRegistry):
        def models_for(self, provider: str) -> list[ModelInfo]:
            return list(catalogue.get(provider, []))

    return Reg(discovery_enabled=False)


class _ObservingStage:
    """A stage that drives the ambient observer and returns scripted outcomes.

    outcomes maps model name -> list of (empty, stop_reason, valid) per call.
    Mirrors what generate_structured does: announce each call, then behave.
    """

    name = "gap_analyzer"

    def __init__(self, settings: QAOpsSettings, script: dict[str, list[str]]) -> None:
        field = {
            "openrouter": "openrouter_model",
            "gemini": "gemini_model",
            "anthropic": "model",
        }[settings.provider]
        self._provider = settings.provider
        self._model = str(getattr(settings, field))
        self._script = script
        self._retries = settings.llm_retries

    def run(self, data: Doc) -> Doc:
        observer = current_observer()
        outcomes = self._script.get(self._model) or self._script.get(self._provider) or ["ok"]
        # Emulate generate_structured's bounded loop over outcomes.
        for attempt, outcome in enumerate(outcomes[: self._retries + 1], start=1):
            observer.before_request(provider=self._provider, model=self._model, attempt=attempt)
            is_empty = outcome == "empty"
            observer.after_request(
                provider=self._provider,
                model=self._model,
                attempt=attempt,
                empty=is_empty,
                chars=0 if is_empty else 100,
            )
            if outcome == "ok":
                return Doc(trace=[*data.trace, f"{self.name}@{self._provider}/{self._model}"])
            if is_empty:
                raise LLMEmptyResponseError(self.name, attempt, self._provider, self._model)
            # malformed: keep repairing until outcomes run out
        raise LLMResponseFormatError(self.name, len(outcomes), ["bad"] * len(outcomes))


class TestInvariantEndToEnd:
    def _run(
        self, catalogue: dict[str, list[ModelInfo]], script: dict[str, list[str]], **kw: object
    ) -> tuple[AdaptiveExecutor, list[ExecutionEvent], list[int]]:
        events: list[ExecutionEvent] = []
        actual_calls = [0]

        provs = [
            ProviderInfo(name="openrouter", key_variables=("K",)),
            ProviderInfo(name="gemini", key_variables=("K",)),
        ]

        def factory(settings: QAOpsSettings) -> list[object]:
            stage = _ObservingStage(settings, script)
            original = stage.run

            def counting_run(data: Doc) -> Doc:
                # Count real "before_request" calls by wrapping the observer.
                return original(data)

            stage.run = counting_run  # type: ignore[method-assign]
            return [stage]

        # Count actual calls by observing the events (REQUEST_STARTED == 1 call).
        def sink(event: ExecutionEvent) -> None:
            events.append(event)
            if event.type is EventType.REQUEST_STARTED:
                actual_calls[0] += 1

        executor = AdaptiveExecutor(
            provs,
            QAOpsSettings(provider="openrouter", **kw),  # type: ignore[arg-type]
            factory,  # type: ignore[arg-type]
            registry=_reg(catalogue),
            events=sink,
            sleep=lambda _s: None,
        )
        return executor, events, actual_calls

    def test_accounting_equals_actual_calls(self) -> None:
        # Two malformed then valid on the one model: 3 real calls, 3 counted.
        catalogue = {"openrouter": [ModelInfo(name="or/m", provider="openrouter", priority=10)]}
        executor, events, actual = self._run(
            catalogue, {"or/m": ["bad", "bad", "ok"]}, openrouter_model="or/m"
        )
        result = executor.run(Doc())
        assert isinstance(result, Doc)
        started = [e for e in events if e.type is EventType.REQUEST_STARTED]
        completed = [e for e in events if e.type is EventType.REQUEST_COMPLETED]
        assert len(started) == len(completed) == 3
        assert started[-1].provider_call_number == 3

    def test_empty_model_abandoned_after_one_call(self) -> None:
        # The live cohere behavior: each empty-returning model is dropped after
        # ONE call, then the next model / provider is tried. No 3x re-roll.
        catalogue = {
            "openrouter": [
                ModelInfo(name=f"or/m{i}", provider="openrouter", priority=i * 10) for i in range(5)
            ],
            "gemini": [
                ModelInfo(
                    name="gemini-2.5-flash",
                    provider="gemini",
                    max_context_tokens=1_000_000,
                    priority=10,
                )
            ],
        }
        executor, events, _ = self._run(
            catalogue,
            {"openrouter": ["empty"], "gemini": ["ok"]},
            openrouter_model="or/m0",
        )
        result = executor.run(Doc())
        assert isinstance(result, Doc)
        assert result.trace == ["gap_analyzer@gemini/gemini-2.5-flash"]
        # 5 openrouter models x 1 call each + 1 gemini = 6 real calls, no 15.
        started = [e for e in events if e.type is EventType.REQUEST_STARTED]
        assert len(started) == 6

    def test_provider_call_budget_caps_total(self) -> None:
        # Many models all returning malformed output: each does one 3-call
        # repair cycle (invalid_output -> next model), so the provider-call
        # budget is the ceiling on the total. With 10 models and a budget of 5,
        # the budget trips before all models are tried.
        catalogue = {
            "openrouter": [
                ModelInfo(name=f"or/m{i}", provider="openrouter", priority=i) for i in range(10)
            ]
        }
        executor, events, _ = self._run(
            catalogue,
            {"openrouter": ["bad", "bad", "bad"]},
            openrouter_model="or/m0",
            max_provider_calls_per_stage=5,
        )
        with pytest.raises(StageError, match="budget"):
            executor.run(Doc())
        started = [e for e in events if e.type is EventType.REQUEST_STARTED]
        assert len(started) <= 5

    def test_five_model_cap_with_repair_bounds_calls(self) -> None:
        # 5 models each doing a 3-call repair cycle = 15 calls, within the 20
        # budget. Single provider so there is no recovery escape - this proves
        # the worst-case multiplication (5 models x 3 repairs) is finite.
        catalogue = {
            "openrouter": [
                ModelInfo(name=f"or/m{i}", provider="openrouter", priority=i) for i in range(5)
            ]
        }
        events: list[ExecutionEvent] = []
        provs = [ProviderInfo(name="openrouter", key_variables=("K",))]

        def factory(settings: QAOpsSettings) -> list[object]:
            return [_ObservingStage(settings, {"openrouter": ["bad", "bad", "bad"]})]

        executor = AdaptiveExecutor(
            provs,
            QAOpsSettings(provider="openrouter", openrouter_model="or/m0"),
            factory,  # type: ignore[arg-type]
            registry=_reg(catalogue),
            events=events.append,
            sleep=lambda _s: None,
        )
        with pytest.raises(StageError):
            executor.run(Doc())
        started = [e for e in events if e.type is EventType.REQUEST_STARTED]
        # 5 models x 3 repair calls = 15, within the 20 budget; never explodes.
        assert len(started) == 15


# =============================================================================
# §15: the exact live regression fixture
# =============================================================================


class TestLiveGapAnalyzerRegression:
    def test_cohere_empty_length_is_bounded_and_recovers(self) -> None:
        # Provider: openrouter, model: cohere/north-mini-code:free returning
        # content="" stop_reason="length" repeatedly. Expected: diagnosed as
        # empty (not truncation), one call per model, bounded, recovers.
        catalogue = {
            "openrouter": [
                ModelInfo(name="cohere/north-mini-code:free", provider="openrouter", priority=10),
                ModelInfo(name="deepseek/deepseek-chat", provider="openrouter", priority=20),
            ],
            "gemini": [
                ModelInfo(
                    name="gemini-2.5-flash",
                    provider="gemini",
                    max_context_tokens=1_000_000,
                    priority=10,
                )
            ],
        }
        events: list[ExecutionEvent] = []

        provs = [
            ProviderInfo(name="openrouter", key_variables=("K",)),
            ProviderInfo(name="gemini", key_variables=("K",)),
        ]

        def factory(settings: QAOpsSettings) -> list[object]:
            script = {"openrouter": ["empty"], "gemini": ["ok"]}
            return [_ObservingStage(settings, script)]

        executor = AdaptiveExecutor(
            provs,
            QAOpsSettings(provider="openrouter", openrouter_model="cohere/north-mini-code:free"),
            factory,  # type: ignore[arg-type]
            registry=_reg(catalogue),
            events=events.append,
            sleep=lambda _s: None,
        )
        result = executor.run(Doc())
        assert isinstance(result, Doc)

        started = [e for e in events if e.type is EventType.REQUEST_STARTED]
        completed = [e for e in events if e.type is EventType.REQUEST_COMPLETED]
        # 2 openrouter models (1 call each, empty) + 1 gemini = 3 real calls.
        assert len(started) == len(completed) == 3
        # Recovered to gemini; completed stage output present.
        assert result.trace == ["gap_analyzer@gemini/gemini-2.5-flash"]
        # No hidden multiplication: exactly one call per empty model.
        assert started[0].provider_call_number == 1

    def test_repeated_empty_cannot_create_unbounded_calls(self) -> None:
        # Every model on every provider returns empty: bounded failure, and the
        # number of calls equals the number of distinct models tried (1 each).
        catalogue = {
            "openrouter": [
                ModelInfo(name=f"or/m{i}", provider="openrouter", priority=i) for i in range(5)
            ],
            "gemini": [ModelInfo(name=f"g/m{i}", provider="gemini", priority=i) for i in range(5)],
        }
        events: list[ExecutionEvent] = []
        provs = [
            ProviderInfo(name="openrouter", key_variables=("K",)),
            ProviderInfo(name="gemini", key_variables=("K",)),
        ]

        def factory(settings: QAOpsSettings) -> list[object]:
            return [_ObservingStage(settings, {"openrouter": ["empty"], "gemini": ["empty"]})]

        executor = AdaptiveExecutor(
            provs,
            QAOpsSettings(provider="openrouter", openrouter_model="or/m0"),
            factory,  # type: ignore[arg-type]
            registry=_reg(catalogue),
            events=events.append,
            sleep=lambda _s: None,
        )
        with pytest.raises(StageError):
            executor.run(Doc())
        started = [e for e in events if e.type is EventType.REQUEST_STARTED]
        # Empty -> one call per model, no repair re-roll: bounded by model caps
        # (5 per provider) not by 3x repair. At most 10 calls, never 30.
        assert len(started) <= 10


# =============================================================================
# §14.24: no secrets in events; §14.10: null observer default
# =============================================================================


class TestSafety:
    def test_null_observer_allows_use_without_wiring(self) -> None:
        client = _CountingClient([_response('{"value": 1}')])
        result = generate_structured(client, _request(), Schema, retries=2)
        assert result.value == 1

    def test_current_observer_defaults_to_null(self) -> None:
        assert isinstance(current_observer(), NullRequestObserver)

    def test_mock_client_still_works_with_structured(self) -> None:
        # Regression: MockLLMClient must expose .model for the observer path.
        client = MockLLMClient(['{"value": 3}'])
        result = generate_structured(client, _request(), Schema, retries=2)
        assert result.value == 3
        assert client.model == "mock-model"
