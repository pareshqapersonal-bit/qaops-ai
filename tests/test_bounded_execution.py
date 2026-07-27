"""Phase 16.1 tests: bounded, ranked, capability-aware execution (ADR-029).

Reproduces the live failure (hundreds of discovered models cycling on credit
exhaustion) and proves execution stays bounded. Also covers the selector's
filtering and ranking, both recovery budgets, and structured events."""

import contextlib

import pytest
from pydantic import BaseModel

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.execution import AdaptiveExecutor, ModelRegistry, ProviderInfo
from qaops.execution.events import EventType, ExecutionEvent
from qaops.execution.models import ModelInfo
from qaops.execution.selector import (
    StageRequirements,
    rejection_reasons,
    select_candidates,
)

CREDIT = "Error code: 402 - requires more credits, can only afford 15461"
TIMEOUT = "Request timed out"
BAD_OUTPUT = "Model output failed validation against X"
UNAVAILABLE = "Error code: 404 - model is unavailable"


class Doc(BaseModel):
    trace: list[str] = []


def run_doc(executor: AdaptiveExecutor, data: Doc) -> Doc:
    """Run and narrow the BaseModel result to Doc for assertions."""
    result = executor.run(data)
    assert isinstance(result, Doc)
    return result


def big_catalogue(n: int, provider: str = "openrouter") -> list[ModelInfo]:
    return [
        ModelInfo(
            name=f"vendor/m{i:03d}",
            provider=provider,
            max_context_tokens=64_000,
            max_output_tokens=8_192,
            priority=100,
        )
        for i in range(n)
    ]


class RegistryWith(ModelRegistry):
    def __init__(self, catalogue: dict[str, list[ModelInfo]]) -> None:
        super().__init__(discovery_enabled=False)
        self._catalogue = catalogue

    def models_for(self, provider: str) -> list[ModelInfo]:
        return list(self._catalogue.get(provider, []))


class FailingStage:
    def __init__(self, name: str, settings: QAOpsSettings, fail_on: dict[str, str]) -> None:
        self.name = name
        self._provider = settings.provider
        self._fail_on = fail_on
        self.calls: list[str] = []

    def run(self, data: Doc) -> Doc:
        message = self._fail_on.get(self._provider)
        if message:
            raise RuntimeError(message)
        return Doc(trace=[*data.trace, f"{self.name}@{self._provider}"])


class TestSelector:
    def test_bounds_a_huge_catalogue(self) -> None:
        pool = select_candidates(big_catalogue(300), StageRequirements(), limit=5)
        assert len(pool) == 5

    def test_deterministic_across_orderings(self) -> None:
        import random

        catalogue = big_catalogue(50)
        shuffled = catalogue[:]
        random.shuffle(shuffled)
        a = [s.model.name for s in select_candidates(catalogue, StageRequirements(), limit=5)]
        b = [s.model.name for s in select_candidates(shuffled, StageRequirements(), limit=5)]
        assert a == b

    def test_configured_model_ranks_first(self) -> None:
        catalogue = big_catalogue(20)
        catalogue.append(ModelInfo(name="preferred/model", provider="openrouter", priority=100))
        pool = select_candidates(
            catalogue, StageRequirements(), limit=5, configured="preferred/model"
        )
        assert pool[0].model.name == "preferred/model"

    def test_filters_models_without_structured_output(self) -> None:
        models = [
            ModelInfo(name="good", provider="x"),
            ModelInfo(name="bad", provider="x", structured_output=False),
        ]
        pool = select_candidates(models, StageRequirements(needs_structured_output=True), limit=5)
        assert [s.model.name for s in pool] == ["good"]

    def test_filters_models_with_insufficient_context(self) -> None:
        models = [
            ModelInfo(name="small", provider="x", max_context_tokens=1_000),
            ModelInfo(name="big", provider="x", max_context_tokens=200_000),
        ]
        pool = select_candidates(models, StageRequirements(min_context_chars=100_000), limit=5)
        assert [s.model.name for s in pool] == ["big"]

    def test_structured_output_capable_preferred(self) -> None:
        # When both are offered but one lacks structured output, the capable one
        # ranks ahead (and the incapable one is filtered when required).
        models = [
            ModelInfo(name="capable", provider="x", structured_output=True, priority=90),
            ModelInfo(name="incapable", provider="x", structured_output=False, priority=10),
        ]
        pool = select_candidates(models, StageRequirements(needs_structured_output=False), limit=5)
        assert pool[0].model.name == "capable"

    def test_rejection_reasons_explain(self) -> None:
        models = [ModelInfo(name="bad", provider="x", structured_output=False)]
        reasons = rejection_reasons(models, StageRequirements(needs_structured_output=True))
        assert "structured-output" in reasons["bad"]


class TestBoundedExecution:
    def _executor(
        self, catalogue: dict[str, list[ModelInfo]], fail_on: dict[str, str], **settings_kw: object
    ) -> tuple[AdaptiveExecutor, list[int]]:
        providers = [
            ProviderInfo(name="openrouter", key_variables=("K",)),
            ProviderInfo(name="gemini", key_variables=("K",)),
        ]
        counter = [0]

        def factory(s: QAOpsSettings) -> list[FailingStage]:
            counter[0] += 1
            return [FailingStage("scenario_generator", s, fail_on)]

        settings = QAOpsSettings(provider="openrouter", **settings_kw)  # type: ignore[arg-type]
        executor = AdaptiveExecutor(
            providers,
            settings,
            factory,  # type: ignore[arg-type]
            registry=RegistryWith(catalogue),
            sleep=lambda _s: None,
        )
        return executor, counter

    def test_hundreds_of_models_do_not_cause_hundreds_of_attempts(self) -> None:
        catalogue = {
            "openrouter": big_catalogue(300),
            "gemini": [ModelInfo(name="gemini-2.5-flash", provider="gemini", priority=10)],
        }
        attempts = [0]

        class Counting(FailingStage):
            def run(self, data: Doc) -> Doc:
                attempts[0] += 1
                return super().run(data)

        providers = [
            ProviderInfo(name="openrouter", key_variables=("K",)),
            ProviderInfo(name="gemini", key_variables=("K",)),
        ]

        def factory(s: QAOpsSettings) -> list[Counting]:
            return [Counting("scenario_generator", s, {"openrouter": CREDIT})]

        executor = AdaptiveExecutor(
            providers,
            QAOpsSettings(provider="openrouter"),
            factory,  # type: ignore[arg-type]
            registry=RegistryWith(catalogue),
            sleep=lambda _s: None,
        )
        result = run_doc(executor, Doc())
        # 5 openrouter models (default cap) + 1 successful gemini = 6, not 300+.
        assert attempts[0] <= 6
        assert result.trace == ["scenario_generator@gemini"]

    def test_per_provider_model_cap_respected(self) -> None:
        catalogue = {
            "openrouter": big_catalogue(300),
            "gemini": [ModelInfo(name="gemini-2.5-flash", provider="gemini", priority=10)],
        }
        executor, _ = self._executor(
            catalogue, {"openrouter": CREDIT}, max_models_per_provider_per_stage=3
        )
        executor.run(Doc())
        # Exactly 3 distinct openrouter models attempted before switching.
        assert executor.report.health["openrouter"].failures == 3

    def test_global_recovery_budget_respected(self) -> None:
        # Both providers credit-exhausted with large catalogues: the stage
        # budget stops recovery even though model caps would allow more.
        catalogue = {
            "openrouter": big_catalogue(300),
            "gemini": big_catalogue(300, provider="gemini"),
        }
        executor, _ = self._executor(
            catalogue,
            {"openrouter": CREDIT, "gemini": CREDIT},
            max_models_per_provider_per_stage=5,
            max_stage_recovery_attempts=6,
        )
        try:
            executor.run(Doc())
        except StageError as exc:
            assert "recovery budget exhausted" in str(exc)
        else:
            raise AssertionError("expected StageError")

    def test_no_infinite_loop_single_provider_all_credit(self) -> None:
        catalogue = {"openrouter": big_catalogue(300)}
        providers = [ProviderInfo(name="openrouter", key_variables=("K",))]

        def factory(s: QAOpsSettings) -> list[FailingStage]:
            return [FailingStage("scenario_generator", s, {"openrouter": CREDIT})]

        executor = AdaptiveExecutor(
            providers,
            QAOpsSettings(provider="openrouter"),
            factory,  # type: ignore[arg-type]
            registry=RegistryWith(catalogue),
            sleep=lambda _s: None,
        )
        with pytest.raises(StageError):
            executor.run(Doc())

    def test_model_specific_failure_tries_another_model(self) -> None:
        # Credit failure on the first model, success on a later one, same provider.
        catalogue = {
            "openrouter": [
                ModelInfo(name="a/first", provider="openrouter", priority=10),
                ModelInfo(name="b/second", provider="openrouter", priority=20),
            ]
        }
        providers = [ProviderInfo(name="openrouter", key_variables=("K",))]

        class FirstFails:
            def __init__(self, name: str, settings: QAOpsSettings) -> None:
                self.name = name
                self._model = settings.openrouter_model

            def run(self, data: Doc) -> Doc:
                if self._model == "a/first":
                    raise RuntimeError(CREDIT)
                return Doc(trace=[*data.trace, f"{self.name}@{self._model}"])

        def factory(s: QAOpsSettings) -> list[FirstFails]:
            return [FirstFails("scenario_generator", s)]

        executor = AdaptiveExecutor(
            providers,
            QAOpsSettings(provider="openrouter", openrouter_model="a/first"),
            factory,  # type: ignore[arg-type]
            registry=RegistryWith(catalogue),
            sleep=lambda _s: None,
        )
        result = run_doc(executor, Doc())
        assert result.trace == ["scenario_generator@b/second"]

    def test_unavailable_model_is_dropped(self) -> None:
        catalogue = {
            "openrouter": [
                ModelInfo(name="a/gone", provider="openrouter", priority=10),
                ModelInfo(name="b/ok", provider="openrouter", priority=20),
            ]
        }
        providers = [ProviderInfo(name="openrouter", key_variables=("K",))]

        class GoneFails:
            def __init__(self, name: str, settings: QAOpsSettings) -> None:
                self.name = name
                self._model = settings.openrouter_model

            def run(self, data: Doc) -> Doc:
                if self._model == "a/gone":
                    raise RuntimeError(UNAVAILABLE)
                return Doc(trace=[*data.trace, self._model])

        def factory(s: QAOpsSettings) -> list[GoneFails]:
            return [GoneFails("scenario_generator", s)]

        executor = AdaptiveExecutor(
            providers,
            QAOpsSettings(provider="openrouter", openrouter_model="a/gone"),
            factory,  # type: ignore[arg-type]
            registry=RegistryWith(catalogue),
            sleep=lambda _s: None,
        )
        result = run_doc(executor, Doc())
        assert result.trace == ["b/ok"]

    def test_completed_stages_not_rerun_during_recovery(self) -> None:
        catalogue = {
            "openrouter": big_catalogue(300),
            "gemini": [ModelInfo(name="gemini-2.5-flash", provider="gemini", priority=10)],
        }
        providers = [
            ProviderInfo(name="openrouter", key_variables=("K",)),
            ProviderInfo(name="gemini", key_variables=("K",)),
        ]
        # Stage 1 succeeds on openrouter; stage 2 credit-fails on openrouter.
        analyze_runs = [0]

        class Stage:
            def __init__(self, name: str, settings: QAOpsSettings) -> None:
                self.name = name
                self._provider = settings.provider

            def run(self, data: Doc) -> Doc:
                if self.name == "analyze":
                    analyze_runs[0] += 1
                    return Doc(trace=[*data.trace, f"analyze@{self._provider}"])
                if self._provider == "openrouter":
                    raise RuntimeError(CREDIT)
                return Doc(trace=[*data.trace, f"scenarios@{self._provider}"])

        def factory(s: QAOpsSettings) -> list[Stage]:
            return [Stage("analyze", s), Stage("scenarios", s)]

        executor = AdaptiveExecutor(
            providers,
            QAOpsSettings(provider="openrouter"),
            factory,  # type: ignore[arg-type]
            registry=RegistryWith(catalogue),
            sleep=lambda _s: None,
        )
        result = run_doc(executor, Doc())
        assert analyze_runs[0] == 1  # not recomputed during scenarios recovery
        assert result.trace == ["analyze@openrouter", "scenarios@gemini"]


class TestExecutionEvents:
    def test_events_emitted_for_stage_and_switches(self) -> None:
        catalogue = {
            "openrouter": big_catalogue(300),
            "gemini": [ModelInfo(name="gemini-2.5-flash", provider="gemini", priority=10)],
        }
        providers = [
            ProviderInfo(name="openrouter", key_variables=("K",)),
            ProviderInfo(name="gemini", key_variables=("K",)),
        ]

        def factory(s: QAOpsSettings) -> list[FailingStage]:
            return [FailingStage("scenario_generator", s, {"openrouter": CREDIT})]

        events: list[ExecutionEvent] = []
        executor = AdaptiveExecutor(
            providers,
            QAOpsSettings(provider="openrouter"),
            factory,  # type: ignore[arg-type]
            registry=RegistryWith(catalogue),
            events=events.append,
            sleep=lambda _s: None,
        )
        executor.run(Doc())
        types = {e.type for e in events}
        assert EventType.STAGE_STARTED in types
        assert EventType.MODEL_FAILED in types
        assert EventType.STAGE_COMPLETED in types
        # A completed event carries stage position for progress.
        completed = next(e for e in events if e.type is EventType.STAGE_COMPLETED)
        assert completed.stage_count >= 1

    def test_events_carry_no_secrets(self) -> None:
        # Event messages are composed from known-safe fields, never raw errors.
        catalogue = {"openrouter": big_catalogue(3)}
        providers = [ProviderInfo(name="openrouter", key_variables=("K",))]

        def factory(s: QAOpsSettings) -> list[FailingStage]:
            return [
                FailingStage(
                    "scenario_generator",
                    s,
                    {"openrouter": "auth failed key sk-secret-abc123456789"},
                )
            ]

        events: list[ExecutionEvent] = []
        executor = AdaptiveExecutor(
            providers,
            QAOpsSettings(provider="openrouter"),
            factory,  # type: ignore[arg-type]
            registry=RegistryWith(catalogue),
            events=events.append,
            sleep=lambda _s: None,
        )
        with contextlib.suppress(StageError):
            executor.run(Doc())
        assert all("sk-secret" not in (e.message or "") for e in events)
