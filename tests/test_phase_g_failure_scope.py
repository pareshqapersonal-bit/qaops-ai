"""Phase G: failure-scope separation (stage-local vs run-sticky).

Proves that genuinely run-sticky failures (authentication, PROVIDER_RATE_LIMIT
daily-quota) keep a provider unavailable for the rest of the run, while
stage-local/transient conditions (model-local failures, and a provider spending
its per-stage model budget) do NOT permanently retire capacity - the provider/
model is reconsidered in later stages.

Reuses the executor test harness (FakeStage/make_factory/executor/run_doc). No
live LLM calls, no API keys.
"""

from qaops.execution.models import ModelRegistry as _MR
from qaops.execution.selector import StageRequirements, _passes_filter
from tests.test_adaptive_execution import (
    AUTH_ERROR,
    BACKUP,
    CREDIT_ERROR,
    GEM_FIRST,
    PRIMARY,
    Doc,
    executor,
    make_factory,
    run_doc,
)

# A daily-quota error (Phase F wording) -> PROVIDER_RATE_LIMIT -> run-sticky.
DAILY_QUOTA_ERROR = "429 RESOURCE_EXHAUSTED GenerateRequestsPerDayPerProject-FreeTier quotaValue 20"
# A server error -> UNKNOWN -> NEXT_MODEL -> model-local, stage-scoped.
SERVER_ERROR = "Error code: 500 - internal server error"


def _openrouter_calls(calls: list[str]) -> int:
    return sum(1 for c in calls if c.startswith("openrouter/"))


# =====================================================================
# Test 1 - a model-local failure does not poison later stages
# Test 10 - cross-stage recovery (the most important scenario)
# =====================================================================


class TestModelFailureStageLocal:
    def test_model_failure_does_not_poison_later_stage(self) -> None:
        # openrouter's first model returns a server error (model-local) - the model
        # is excluded stage-locally and the stage recovers on a sibling. In the next
        # stage openrouter must be fully usable again (stage-local exclusion cleared).
        factory, calls = make_factory(
            [("analyze", {"openrouter/deepseek/deepseek-chat": SERVER_ERROR}), ("scenarios", None)],
            fail_times=1,
        )
        agent = executor([PRIMARY, BACKUP], factory)
        result = run_doc(agent, Doc(text="prd"))
        # Both stages served by openrouter (rebuilt for the later stage), never
        # permanently retired.
        assert result.trace[0].startswith("analyze@openrouter/")
        assert result.trace[1].startswith("scenarios@openrouter/")
        assert _openrouter_calls(calls) >= 2
        # Stage-local exclusions are empty at the end (cleared each stage boundary);
        # run-sticky exclusions never populated by a model-local failure.
        assert agent._excluded_stage["openrouter"] == set()
        assert agent._excluded["openrouter"] == set()

    def test_cross_stage_recovery_mixed_scopes(self) -> None:
        # Stage 1: openrouter first model server-errors once (stage-local), stage
        # recovers on a sibling. Stage 2: openrouter is eligible again. This is the
        # headline Phase G scenario: stage-local failures do not accumulate.
        factory, calls = make_factory(
            [("analyze", {"openrouter/deepseek/deepseek-chat": SERVER_ERROR}), ("scenarios", None)],
            fail_times=1,
        )
        agent = executor([PRIMARY, BACKUP], factory)
        result = run_doc(agent, Doc(text="prd"))
        assert len(result.trace) == 2
        # openrouter serves BOTH stages (re-eligible in stage 2), and its health
        # never went unavailable.
        assert result.trace[0].startswith("analyze@openrouter/")
        assert result.trace[1].startswith("scenarios@openrouter/")
        assert agent.report.health["openrouter"].available is True


# =====================================================================
# Test 2 - provider model-budget exhaustion is stage-local
# =====================================================================


class TestBudgetStageLocal:
    def test_budget_exhaustion_does_not_run_disable_provider(self) -> None:
        # Every openrouter model fails (credit) in stage 1 -> provider budget spent
        # -> provider switches to gemini for stage 1. But provider HEALTH must stay
        # available (stage-local disable), so it is not retired for the run.
        factory, _ = make_factory([("scenarios", {"openrouter": CREDIT_ERROR})])
        agent = executor([PRIMARY, BACKUP], factory)
        run_doc(agent, Doc(text="prd"))
        assert agent.report.health["openrouter"].available is True

    def test_budget_exhausted_provider_retried_in_later_stage(self) -> None:
        # Strong proof: openrouter's whole model set fails (server error) in BOTH
        # stages. Pre-Phase-G, stage 1's budget exhaustion would run-disable
        # openrouter (built once). Now it is re-tried in stage 2 - proving budget
        # exhaustion is stage-local. openrouter is built for both stages.
        factory, calls = make_factory(
            [
                ("analyze", {"openrouter": SERVER_ERROR}),
                ("scenarios", {"openrouter": SERVER_ERROR}),
            ],
            fail_times=99,
        )
        agent = executor([PRIMARY, BACKUP], factory)
        run_doc(agent, Doc(text="prd"))
        # Rebuilt in stage 2 (more than one build) instead of being retired.
        assert _openrouter_calls(calls) > 1
        assert agent.report.health["openrouter"].available is True

    def test_budget_exhausted_provider_reeligible_next_stage(self) -> None:
        # Stage 1: all openrouter models credit-fail -> budget spent -> provider
        # added to the STAGE-LOCAL disabled set and gemini serves. The stage-local
        # set is cleared at the next stage boundary and provider health stays
        # available, so openrouter is eligible again in stage 2.
        factory, _ = make_factory(
            [("analyze", {"openrouter": CREDIT_ERROR}), ("scenarios", None)],
        )
        agent = executor([PRIMARY, BACKUP], factory)
        result = run_doc(agent, Doc(text="prd"))
        assert result.trace[0] == f"analyze@{GEM_FIRST}"  # stage 1 fell to gemini
        # Health never run-disabled (stage-local budget disable), and the
        # stage-local disabled set is empty after the run (cleared each boundary).
        assert agent.report.health["openrouter"].available is True
        assert agent._provider_stage_disabled == set()


# =====================================================================
# Test 3 - daily quota (PROVIDER_RATE_LIMIT) is run-sticky
# Test 4 - authentication is run-sticky
# =====================================================================


class TestRunStickyDisables:
    def test_daily_quota_disables_provider_for_run(self) -> None:
        # openrouter hits daily-quota wording in stage 1 -> PROVIDER_RATE_LIMIT ->
        # run-sticky. It must NOT be rebuilt for stage 2.
        factory, calls = make_factory(
            [("analyze", {"openrouter": DAILY_QUOTA_ERROR}), ("scenarios", None)],
        )
        agent = executor([PRIMARY, BACKUP], factory)
        result = run_doc(agent, Doc(text="prd"))
        assert result.trace == [f"analyze@{GEM_FIRST}", f"scenarios@{GEM_FIRST}"]
        assert agent.report.health["openrouter"].available is False
        assert _openrouter_calls(calls) == 1  # built once, never again

    def test_authentication_disables_provider_for_run(self) -> None:
        factory, calls = make_factory(
            [("analyze", {"openrouter": AUTH_ERROR}), ("scenarios", None)],
        )
        agent = executor([PRIMARY, BACKUP], factory)
        result = run_doc(agent, Doc(text="prd"))
        assert result.trace == [f"analyze@{GEM_FIRST}", f"scenarios@{GEM_FIRST}"]
        assert agent.report.health["openrouter"].available is False
        assert _openrouter_calls(calls) == 1


# =====================================================================
# Test 5 - transient 429 stays retry behaviour (no permanent disable)
# =====================================================================


class TestTransientRetry:
    def test_transient_rate_limit_retries_same_model(self) -> None:
        # A transient 429 (fail once, then succeed) recovers on the SAME model and
        # does not disable the provider or exclude the model.
        factory, _ = make_factory(
            [("scenarios", {"openrouter/deepseek/deepseek-chat": "429 rate-limited upstream"})],
            fail_times=1,
        )
        agent = executor([PRIMARY, BACKUP], factory)
        result = run_doc(agent, Doc(text="prd"))
        assert result.trace == ["scenarios@openrouter/deepseek/deepseek-chat"]
        assert agent.report.health["openrouter"].available is True


# =====================================================================
# Test 6 - capability filtering intact (image vs text)
# =====================================================================


class TestCapabilityIntact:
    def test_image_stage_rejects_text_only_model(self) -> None:
        lite = next(
            m for m in _MR().models_for("gemini") if m.name == "gemini-flash-lite-latest"
        )  # images_supported=False
        ok, reason = _passes_filter(
            lite, StageRequirements(needs_structured_output=True, needs_images=True), set()
        )
        assert ok is False
        assert "image" in reason

    def test_text_stage_admits_multimodal_gemini(self) -> None:
        flash = next(m for m in _MR().models_for("gemini") if m.name == "gemini-flash-latest")
        ok, _ = _passes_filter(flash, StageRequirements(needs_structured_output=True), set())
        assert ok is True


# =====================================================================
# Test 7 - provider ordering unchanged
# =====================================================================


class TestOrderingUnchanged:
    def test_first_provider_is_chain_head(self) -> None:
        # With openrouter first in the provider list, a no-failure run uses it -
        # ordering/preference is unchanged by the lifecycle fix.
        factory, _ = make_factory([("scenarios", None)])
        agent = executor([PRIMARY, BACKUP], factory)
        result = run_doc(agent, Doc(text="prd"))
        assert result.trace[0].startswith("scenarios@openrouter/")


# =====================================================================
# Test 8 - clarification remains independent of executor failure state
# =====================================================================


class TestClarificationIndependent:
    def test_clarification_uses_separate_resilient_call(self) -> None:
        # Clarification routes through resilient_structured_call, which builds its
        # own candidate list / failover loop - it does not read the executor's
        # _excluded / _excluded_stage / _provider_stage_disabled state.
        import inspect

        from qaops.clarification import service as clar_service

        src = inspect.getsource(clar_service)
        assert "resilient_structured_call" in src
        # The clarification module must not touch executor run/stage failure state.
        for attr in ("_excluded_stage", "_provider_stage_disabled", "_models_tried"):
            assert attr not in src


# =====================================================================
# Test 9 - Phase F daily-quota classification regression
# =====================================================================


class TestPhaseFRegression:
    def test_daily_quota_still_provider_rate_limit(self) -> None:
        from qaops.execution.policy import Action, FailureKind, recovery_for_exception

        rec = recovery_for_exception(Exception(DAILY_QUOTA_ERROR))
        assert rec.kind is FailureKind.PROVIDER_RATE_LIMIT
        assert rec.action is Action.DISABLE_AND_SWITCH

    def test_transient_429_still_rate_limit(self) -> None:
        from qaops.execution.policy import Action, FailureKind, recovery_for_exception

        rec = recovery_for_exception(Exception("429 Too Many Requests"))
        assert rec.kind is FailureKind.RATE_LIMIT
        assert rec.action is Action.RETRY_SAME_WITH_BACKOFF
