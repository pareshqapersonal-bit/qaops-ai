"""Phase 16.2 tests: request timeout guard and accurate progress (ADR-030).

Covers the timeout setting, its propagation to every provider client, disabled
SDK retries, timeout-exception normalization, the timeout recovery hierarchy
under the existing bounds, request lifecycle events and counter semantics, and
the live-failure regression (four models fail, the fifth times out). Mock
providers throughout - no real network, no production-length sleeps."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.execution import AdaptiveExecutor, ModelRegistry, ProviderInfo
from qaops.execution.events import EventType, ExecutionEvent
from qaops.execution.models import ModelInfo
from qaops.execution.policy import Action, FailureKind, classify_failure, recovery_for
from qaops.llm.timeouts import is_timeout_exception, normalize_timeout_message

CREDIT = "Error code: 402 - requires more credits, can only afford 15461"
UNAVAILABLE = "Error code: 404 - model is unavailable"
TIMEOUT_MSG = "request timed out after the configured deadline (openrouter): deadline"


class Doc(BaseModel):
    trace: list[str] = []


# --- provider timeout exception types, faked without importing SDKs ----------


class APITimeoutError(Exception):
    """Stands in for anthropic/openai APITimeoutError (matched by class name)."""


class FakeConnectionError(Exception):
    """A non-timeout network error, to prove it is NOT misclassified."""


# =============================================================================
# Section 13.1-13.3: the setting
# =============================================================================


class TestTimeoutSetting:
    def test_default_is_60_seconds(self) -> None:
        assert QAOpsSettings().request_timeout_seconds == 60.0

    def test_custom_value_loads(self) -> None:
        assert QAOpsSettings(request_timeout_seconds=30).request_timeout_seconds == 30.0

    def test_zero_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QAOpsSettings(request_timeout_seconds=0)

    def test_negative_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QAOpsSettings(request_timeout_seconds=-5)

    def test_absurdly_large_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            QAOpsSettings(request_timeout_seconds=100_000)

    def test_old_config_without_the_field_still_loads(self) -> None:
        # Backward compatibility: a config predating the field uses the default.
        settings = QAOpsSettings(provider="openrouter", temperature=0.3)
        assert settings.request_timeout_seconds == 60.0


# =============================================================================
# Section 13.4-13.6, 5: propagation and disabled SDK retries
# =============================================================================


class TestTimeoutPropagation:
    @pytest.fixture(autouse=True)
    def _keys(self) -> Iterator[None]:
        saved = dict(os.environ)
        os.environ["ANTHROPIC_API_KEY"] = "test-key"
        os.environ["OPENROUTER_API_KEY"] = "test-key"
        os.environ["GEMINI_API_KEY"] = "test-key"
        yield
        os.environ.clear()
        os.environ.update(saved)

    def test_anthropic_receives_timeout(self) -> None:
        from qaops.llm.anthropic_client import AnthropicClient

        client = AnthropicClient(model="claude-sonnet-4-6", timeout_seconds=42)
        assert client._sdk.timeout == 42

    def test_openrouter_receives_timeout(self) -> None:
        from qaops.llm.openrouter_client import OpenRouterClient

        client = OpenRouterClient(model="deepseek/deepseek-chat", timeout_seconds=42)
        # OpenRouter uses an async SDK plus a hard wall-clock deadline (ADR-031).
        assert client._async_sdk is not None
        assert client._async_sdk.timeout == 42
        assert client._deadline_seconds == 42

    def test_gemini_receives_timeout_in_milliseconds(self) -> None:
        from qaops.llm.gemini_client import GeminiClient

        client = GeminiClient(model="gemini-2.5-flash", timeout_seconds=42)
        http_options = client._sdk._api_client._http_options
        assert http_options.timeout == 42_000  # seconds -> ms

    def test_factory_passes_configured_timeout(self) -> None:
        from qaops.llm.factory import create_client

        settings = QAOpsSettings(provider="anthropic", request_timeout_seconds=25)
        client = create_client(settings)
        assert client._sdk.timeout == 25  # type: ignore[attr-defined]

    def test_anthropic_sdk_retries_disabled(self) -> None:
        from qaops.llm.anthropic_client import AnthropicClient

        client = AnthropicClient(model="claude-sonnet-4-6")
        assert client._sdk.max_retries == 0

    def test_openrouter_sdk_retries_disabled(self) -> None:
        from qaops.llm.openrouter_client import OpenRouterClient

        client = OpenRouterClient(model="deepseek/deepseek-chat")
        assert client._async_sdk is not None
        assert client._async_sdk.max_retries == 0


# =============================================================================
# Section 6: timeout normalization without misclassifying network errors
# =============================================================================


class TestTimeoutNormalization:
    def test_sdk_timeout_type_detected(self) -> None:
        assert is_timeout_exception(APITimeoutError("boom"))

    def test_deadline_text_detected(self) -> None:
        assert is_timeout_exception(Exception("504 deadline exceeded"))

    def test_timed_out_text_detected(self) -> None:
        assert is_timeout_exception(Exception("the request timed out"))

    def test_plain_connection_error_not_a_timeout(self) -> None:
        assert not is_timeout_exception(FakeConnectionError("connection refused"))

    def test_value_error_not_a_timeout(self) -> None:
        assert not is_timeout_exception(ValueError("bad json"))

    def test_normalized_message_classifies_as_timeout(self) -> None:
        message = normalize_timeout_message("openrouter", APITimeoutError("x"))
        assert classify_failure(message) is FailureKind.TIMEOUT

    def test_non_timeout_message_passes_through_unchanged(self) -> None:
        exc = FakeConnectionError("connection refused")
        assert normalize_timeout_message("openrouter", exc) == "connection refused"

    def test_timeout_recovery_retries_same_model(self) -> None:
        recovery = recovery_for(TIMEOUT_MSG)
        assert recovery.action is Action.RETRY_SAME
        assert not recovery.disables_provider
        assert not recovery.disables_model


# =============================================================================
# Shared harness for executor-level timeout tests
# =============================================================================


def _reg(catalogue: dict[str, list[ModelInfo]]) -> ModelRegistry:
    class Reg(ModelRegistry):
        def models_for(self, provider: str) -> list[ModelInfo]:
            return list(catalogue.get(provider, []))

    return Reg(discovery_enabled=False)


def _executor(
    catalogue: dict[str, list[ModelInfo]],
    fail_on: dict[str, str],
    *,
    events: list[ExecutionEvent] | None = None,
    providers: list[ProviderInfo] | None = None,
    **settings_kw: object,
) -> AdaptiveExecutor:
    provs = providers or [
        ProviderInfo(name="openrouter", key_variables=("K",)),
        ProviderInfo(name="gemini", key_variables=("K",)),
    ]

    def factory(settings: QAOpsSettings) -> list[object]:
        field = {
            "openrouter": "openrouter_model",
            "gemini": "gemini_model",
            "anthropic": "model",
        }[settings.provider]
        model_name = str(getattr(settings, field))

        class Stage:
            name = "requirement_analyzer"

            def run(self, data: Doc) -> Doc:
                # A faithful stage announces its provider call to the ambient
                # observer, exactly as run_structured_stage does, so request
                # lifecycle events and the provider-call budget see it (ADR-030).
                from qaops.llm.request_budget import current_observer

                observer = current_observer()
                observer.before_request(provider=settings.provider, model=model_name, attempt=1)
                message = fail_on.get(model_name) or fail_on.get(settings.provider)
                observer.after_request(
                    provider=settings.provider,
                    model=model_name,
                    attempt=1,
                    empty=False,
                    chars=0 if message else 100,
                )
                if message:
                    raise RuntimeError(message)
                return Doc(trace=[*data.trace, f"{self.name}@{settings.provider}/{model_name}"])

        return [Stage()]

    return AdaptiveExecutor(
        provs,
        QAOpsSettings(provider=provs[0].name, **settings_kw),  # type: ignore[arg-type]
        factory,  # type: ignore[arg-type]
        registry=_reg(catalogue),
        events=(events.append if events is not None else None),
        sleep=lambda _s: None,
    )


def run_doc(executor: AdaptiveExecutor, data: Doc) -> Doc:
    result = executor.run(data)
    assert isinstance(result, Doc)
    return result


# =============================================================================
# Section 13.8-13.14, 7: timeout recovery under existing bounds
# =============================================================================


class TestTimeoutRecovery:
    def test_timeout_retries_same_model_then_succeeds(self) -> None:
        # Model times out twice, then succeeds on the third attempt (within the
        # default max_attempts_per_model=3).
        calls = {"n": 0}
        providers = [ProviderInfo(name="openrouter", key_variables=("K",))]

        def factory(settings: QAOpsSettings) -> list[object]:
            class Stage:
                name = "requirement_analyzer"

                def run(self, data: Doc) -> Doc:
                    calls["n"] += 1
                    if calls["n"] < 3:
                        raise RuntimeError(TIMEOUT_MSG)
                    return Doc(trace=[*data.trace, "ok"])

            return [Stage()]

        executor = AdaptiveExecutor(
            providers,
            QAOpsSettings(provider="openrouter", openrouter_model="deepseek/deepseek-chat"),
            factory,  # type: ignore[arg-type]
            registry=_reg(
                {"openrouter": [ModelInfo(name="deepseek/deepseek-chat", provider="openrouter")]}
            ),
            sleep=lambda _s: None,
        )
        result = run_doc(executor, Doc())
        assert result.trace == ["ok"]
        assert calls["n"] == 3  # two timeouts, then success on the same model

    def test_same_model_timeout_retries_are_bounded(self) -> None:
        # A model that always times out, single provider, single model: the
        # same-model retry cap and provider exhaustion terminate it.
        providers = [ProviderInfo(name="openrouter", key_variables=("K",))]
        executor = _executor(
            {"openrouter": [ModelInfo(name="deepseek/deepseek-chat", provider="openrouter")]},
            {"deepseek/deepseek-chat": TIMEOUT_MSG},
            providers=providers,
        )
        with pytest.raises(StageError):
            executor.run(Doc())

    def test_exhausted_model_timeout_moves_to_next_model(self) -> None:
        catalogue = {
            "openrouter": [
                ModelInfo(name="a/first", provider="openrouter", priority=10),
                ModelInfo(name="b/second", provider="openrouter", priority=20),
            ]
        }
        providers = [ProviderInfo(name="openrouter", key_variables=("K",))]
        executor = _executor(
            catalogue,
            {"a/first": TIMEOUT_MSG},
            providers=providers,
            openrouter_model="a/first",
        )
        result = run_doc(executor, Doc())
        assert result.trace == ["requirement_analyzer@openrouter/b/second"]

    def test_per_provider_model_cap_still_applies(self) -> None:
        # 20 models all timing out: at most 5 distinct models tried before the
        # provider is exhausted.
        catalogue = {
            "openrouter": [
                ModelInfo(name=f"m/{i:02d}", provider="openrouter", priority=i) for i in range(20)
            ],
            "gemini": [ModelInfo(name="gemini-2.5-flash", provider="gemini", priority=10)],
        }
        executor = _executor(catalogue, {"openrouter": TIMEOUT_MSG})
        result = run_doc(executor, Doc())
        assert result.trace[0].startswith("requirement_analyzer@gemini")
        # 5 distinct models tried on openrouter (the cap), not 20.
        distinct = {frm for (_s, frm, _t) in executor.report.model_switches}
        assert len(distinct) <= 5

    def test_stage_recovery_cap_still_applies(self) -> None:
        catalogue = {
            "openrouter": [
                ModelInfo(name=f"m/{i:02d}", provider="openrouter", priority=i) for i in range(20)
            ],
            "gemini": [
                ModelInfo(name=f"g/{i:02d}", provider="gemini", priority=i) for i in range(20)
            ],
        }
        executor = _executor(
            catalogue,
            {"openrouter": TIMEOUT_MSG, "gemini": TIMEOUT_MSG},
            max_stage_recovery_attempts=6,
            # Give ample provider-call headroom so the RECOVERY cap is what
            # trips here (the two bounds are independent; this test targets
            # recovery). 7 models x 3 same-model retries = 21 worst case.
            max_provider_calls_per_stage=100,
        )
        with pytest.raises(StageError, match="recovery budget exhausted"):
            executor.run(Doc())

    def test_timeout_cannot_cause_infinite_loop(self) -> None:
        # Single provider, single model, permanent timeout: must terminate.
        providers = [ProviderInfo(name="openrouter", key_variables=("K",))]
        executor = _executor(
            {"openrouter": [ModelInfo(name="only/model", provider="openrouter")]},
            {"only/model": TIMEOUT_MSG},
            providers=providers,
        )
        with pytest.raises(StageError):
            executor.run(Doc())

    def test_completed_stages_not_rerun_after_timeout(self) -> None:
        catalogue = {
            "openrouter": [ModelInfo(name="or/model", provider="openrouter", priority=10)],
            "gemini": [ModelInfo(name="gemini-2.5-flash", provider="gemini", priority=10)],
        }
        analyze_runs = {"n": 0}
        providers = [
            ProviderInfo(name="openrouter", key_variables=("K",)),
            ProviderInfo(name="gemini", key_variables=("K",)),
        ]

        def factory(settings: QAOpsSettings) -> list[object]:
            class Analyze:
                name = "analyze"

                def run(self, data: Doc) -> Doc:
                    analyze_runs["n"] += 1
                    return Doc(trace=[*data.trace, f"analyze@{settings.provider}"])

            class Scenarios:
                name = "scenarios"

                def run(self, data: Doc) -> Doc:
                    if settings.provider == "openrouter":
                        raise RuntimeError(TIMEOUT_MSG)
                    return Doc(trace=[*data.trace, f"scenarios@{settings.provider}"])

            return [Analyze(), Scenarios()]

        executor = AdaptiveExecutor(
            providers,
            QAOpsSettings(provider="openrouter"),
            factory,  # type: ignore[arg-type]
            registry=_reg(catalogue),
            sleep=lambda _s: None,
        )
        result = run_doc(executor, Doc())
        assert analyze_runs["n"] == 1  # not recomputed during scenarios recovery
        assert result.trace == ["analyze@openrouter", "scenarios@gemini"]


# =============================================================================
# Section 14: the live-failure regression
# =============================================================================


class TestLiveFailureRegression:
    def _catalogue(self) -> dict[str, list[ModelInfo]]:
        return {
            "openrouter": [
                ModelInfo(name="deepseek/deepseek-chat", provider="openrouter", priority=10),
                ModelInfo(name="openai/gpt-4o-mini", provider="openrouter", priority=20),
                ModelInfo(name="anthropic/claude-3.5-sonnet", provider="openrouter", priority=30),
                ModelInfo(
                    name="meta-llama/llama-3.3-70b-instruct", provider="openrouter", priority=40
                ),
                ModelInfo(name="cohere/north-mini-code:free", provider="openrouter", priority=50),
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

    def _fail_map(self) -> dict[str, str]:
        return {
            "deepseek/deepseek-chat": CREDIT,
            "openai/gpt-4o-mini": CREDIT,
            "anthropic/claude-3.5-sonnet": UNAVAILABLE,
            "meta-llama/llama-3.3-70b-instruct": CREDIT,
            "cohere/north-mini-code:free": TIMEOUT_MSG,  # the model that hung
        }

    def test_fifth_model_times_out_and_recovers_to_gemini(self) -> None:
        events: list[ExecutionEvent] = []
        executor = _executor(
            self._catalogue(),
            self._fail_map(),
            events=events,
            openrouter_model="deepseek/deepseek-chat",
        )
        result = run_doc(executor, Doc())

        # Recovered to Gemini rather than hanging on the fifth model.
        assert result.trace == ["requirement_analyzer@gemini/gemini-2.5-flash"]
        # The hanging model produced timeout events, not an indefinite wait.
        assert any(e.type is EventType.REQUEST_TIMED_OUT for e in events)

    def test_bounds_all_hold_in_the_live_scenario(self) -> None:
        executor = _executor(
            self._catalogue(),
            self._fail_map(),
            openrouter_model="deepseek/deepseek-chat",
        )
        run_doc(executor, Doc())

        # Five distinct OpenRouter models attempted (the cap), then a switch.
        distinct = {frm for (_s, frm, _t) in executor.report.model_switches}
        assert len(distinct) <= 5
        assert len(executor.report.provider_switches) == 1
        assert executor.report.provider_switches[0][1] == "openrouter"
        assert executor.report.provider_switches[0][2] == "gemini"

    def test_same_model_timeout_retries_bounded_in_live_scenario(self) -> None:
        # The fifth model times out; its same-model retries are capped at
        # max_attempts_per_model (default 3) before moving on.
        events: list[ExecutionEvent] = []
        executor = _executor(
            self._catalogue(),
            self._fail_map(),
            events=events,
            openrouter_model="deepseek/deepseek-chat",
        )
        run_doc(executor, Doc())
        timeouts = [e for e in events if e.type is EventType.REQUEST_TIMED_OUT]
        # cohere is the only timeout model; it is tried at most 3 times.
        assert 1 <= len(timeouts) <= 3

    def test_no_infinite_loop_when_gemini_also_unavailable(self) -> None:
        catalogue = self._catalogue()
        fail = self._fail_map()
        fail["gemini-2.5-flash"] = TIMEOUT_MSG
        executor = _executor(catalogue, fail, openrouter_model="deepseek/deepseek-chat")
        with pytest.raises(StageError):
            executor.run(Doc())


# =============================================================================
# Section 13.15-13.19, 10: events and counters
# =============================================================================


class TestExecutionEventLifecycle:
    def _run_with_timeout_then_switch(self) -> list[ExecutionEvent]:
        events: list[ExecutionEvent] = []
        catalogue = {
            "openrouter": [
                ModelInfo(name="a/first", provider="openrouter", priority=10),
                ModelInfo(name="b/second", provider="openrouter", priority=20),
            ],
            "gemini": [ModelInfo(name="gemini-2.5-flash", provider="gemini", priority=10)],
        }
        executor = _executor(
            catalogue,
            {"a/first": TIMEOUT_MSG, "b/second": CREDIT},
            events=events,
            openrouter_model="a/first",
        )
        run_doc(executor, Doc())
        return events

    def test_request_started_emitted(self) -> None:
        events = self._run_with_timeout_then_switch()
        assert any(e.type is EventType.REQUEST_STARTED for e in events)

    def test_request_timed_out_emitted(self) -> None:
        events = self._run_with_timeout_then_switch()
        assert any(e.type is EventType.REQUEST_TIMED_OUT for e in events)

    def test_request_retry_emitted(self) -> None:
        events = self._run_with_timeout_then_switch()
        assert any(e.type is EventType.REQUEST_RETRY for e in events)

    def test_model_switch_emitted(self) -> None:
        events = self._run_with_timeout_then_switch()
        assert any(e.type is EventType.MODEL_SWITCH for e in events)

    def test_provider_switch_emitted(self) -> None:
        events = self._run_with_timeout_then_switch()
        assert any(e.type is EventType.PROVIDER_SWITCH for e in events)

    def test_stage_completed_emitted(self) -> None:
        events = self._run_with_timeout_then_switch()
        assert any(e.type is EventType.STAGE_COMPLETED for e in events)


class TestCounterSemantics:
    def test_request_attempt_increments_on_same_model_retry(self) -> None:
        # A model that times out twice then succeeds: each executor retry is a
        # real provider call, so provider_call_number increments 1..3 while
        # model_attempt_number stays 1 (same model throughout).
        events: list[ExecutionEvent] = []
        calls = {"n": 0}
        providers = [ProviderInfo(name="openrouter", key_variables=("K",))]

        def factory(settings: QAOpsSettings) -> list[object]:
            class Stage:
                name = "requirement_analyzer"

                def run(self, data: Doc) -> Doc:
                    from qaops.llm.request_budget import current_observer

                    observer = current_observer()
                    observer.before_request(provider="openrouter", model="only/model", attempt=1)
                    calls["n"] += 1
                    observer.after_request(
                        provider="openrouter",
                        model="only/model",
                        attempt=1,
                        empty=False,
                        chars=0 if calls["n"] < 3 else 100,
                    )
                    if calls["n"] < 3:
                        raise RuntimeError(TIMEOUT_MSG)
                    return Doc(trace=[*data.trace, "ok"])

            return [Stage()]

        executor = AdaptiveExecutor(
            providers,
            QAOpsSettings(provider="openrouter", openrouter_model="only/model"),
            factory,  # type: ignore[arg-type]
            registry=_reg({"openrouter": [ModelInfo(name="only/model", provider="openrouter")]}),
            events=events.append,
            sleep=lambda _s: None,
        )
        run_doc(executor, Doc())
        started = [e for e in events if e.type is EventType.REQUEST_STARTED]
        # Three real provider calls, numbered 1..3, all on the same model.
        assert [e.provider_call_number for e in started] == [1, 2, 3]
        assert all(e.model_attempt_number == 1 for e in started)

    def test_model_attempt_number_increments_on_model_switch(self) -> None:
        events: list[ExecutionEvent] = []
        catalogue = {
            "openrouter": [
                ModelInfo(name="a/first", provider="openrouter", priority=10),
                ModelInfo(name="b/second", provider="openrouter", priority=20),
            ]
        }
        providers = [ProviderInfo(name="openrouter", key_variables=("K",))]
        executor = _executor(
            catalogue,
            {"a/first": CREDIT},
            events=events,
            providers=providers,
            openrouter_model="a/first",
        )
        run_doc(executor, Doc())
        started = [e for e in events if e.type is EventType.REQUEST_STARTED]
        # First model attempt 1, second model attempt 2.
        assert started[0].model_attempt_number == 1
        assert started[-1].model_attempt_number == 2


# =============================================================================
# Section 11, 22: no secrets in events
# =============================================================================


class TestNoSecretsInEvents:
    def test_event_messages_never_contain_a_key(self) -> None:
        events: list[ExecutionEvent] = []
        providers = [ProviderInfo(name="openrouter", key_variables=("K",))]
        executor = _executor(
            {"openrouter": [ModelInfo(name="only/model", provider="openrouter")]},
            {"only/model": "auth failed with key sk-secret-abc123456789"},
            events=events,
            providers=providers,
        )
        with pytest.raises(StageError):
            executor.run(Doc())
        assert all("sk-secret" not in (e.message or "") for e in events)


# =============================================================================
# Section 13.23: CLI compatibility with the new timeout/event machinery
# =============================================================================


class TestCliCompatibility:
    def test_cli_design_runs_with_timeout_setting(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import json

        from typer.testing import CliRunner

        import qaops.cli.app as appmod
        from qaops.llm import MockLLMClient

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-should-not-leak-123")
        for var in ("OPENROUTER_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        test_conditions = json.dumps(
            {
                "conditions": [
                    {
                        "scenario_id": "SC-001",
                        "requirement_ids": ["REQ-001"],
                        "business_rule_ids": [],
                        "category": "positive",
                        "description": "primary condition",
                        "rationale": "REQ-001",
                        "source_basis": "explicit_requirement",
                        "status": "resolved",
                        "parameters": {},
                        "gap_reference": "",
                    }
                ]
            }
        )
        test_cases = json.dumps(
            {
                "test_cases": [
                    {
                        "scenario_id": "SC-001",
                        "condition_id": "COND-001",
                        "requirement_ids": ["REQ-001"],
                        "title": "t",
                        "expected_result": "r",
                        "steps": [{"action": "a"}],
                        "priority": "high",
                        "test_type": "functional",
                    }
                ]
            }
        )
        d = tmp_path
        csv = d / "s.csv"
        csv.write_text("title,category,requirement_ids\r\nvalid,positive,REQ-001\r\n", newline="")
        monkeypatch.setattr(
            "qaops.services.design_service.create_client",
            lambda settings: MockLLMClient([test_conditions, test_cases]),
        )
        result = CliRunner().invoke(
            appmod.app,
            ["design", str(csv), "-o", str(d / "out"), "-f", "json"],
        )
        assert result.exit_code == 0, result.output
        # CLI still renders adaptive progress, and no secret appears.
        assert "test_case_generator" in result.output
        assert "sk-secret" not in result.output
