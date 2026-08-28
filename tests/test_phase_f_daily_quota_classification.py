"""Phase F: daily-quota vs transient rate-limit classification.

Genuine per-project/day quota exhaustion (Gemini free-tier
"GenerateRequestsPerDayPerProject") must classify as PROVIDER_RATE_LIMIT ->
DISABLE_AND_SWITCH, so the exhausted provider is abandoned for the rest of the run
instead of being retried. Transient 429s - including Gemini's per-MINUTE
RESOURCE_EXHAUSTED - must remain RATE_LIMIT -> RETRY_SAME_WITH_BACKOFF.

Pure classifier/policy tests: no live calls, no API keys.
"""

from qaops.execution.policy import (
    Action,
    FailureKind,
    classify_failure,
    recovery_for,
    recovery_for_exception,
)

# The exact production error observed (RESOURCE_EXHAUSTED, per-project/day quota).
GEMINI_DAILY = (
    "gemini/gemini-3-flash-preview: 429 RESOURCE_EXHAUSTED. "
    "Quota metric: generativelanguage.googleapis.com/generate_content_free_tier_requests "
    "Quota: GenerateRequestsPerDayPerProject-FreeTier model: gemini-3-flash quotaValue: 20"
)

# A Gemini RESOURCE_EXHAUSTED that is a per-MINUTE (transient) limit, NOT daily.
GEMINI_PER_MINUTE = (
    "gemini: 429 RESOURCE_EXHAUSTED Quota: GenerateRequestsPerMinutePerProject-FreeTier limit 15"
)


class TestGeminiDailyQuota:
    def test_gemini_daily_is_provider_rate_limit(self) -> None:
        assert classify_failure(GEMINI_DAILY) is FailureKind.PROVIDER_RATE_LIMIT

    def test_gemini_daily_action_is_disable_and_switch(self) -> None:
        rec = recovery_for_exception(Exception(GEMINI_DAILY))
        assert rec.kind is FailureKind.PROVIDER_RATE_LIMIT
        assert rec.action is Action.DISABLE_AND_SWITCH

    def test_gemini_daily_is_not_transient_retry(self) -> None:
        rec = recovery_for_exception(Exception(GEMINI_DAILY))
        assert rec.action is not Action.RETRY_SAME_WITH_BACKOFF

    def test_requestsperday_wording_matches(self) -> None:
        # The unspaced "RequestsPerDayPerProject" wording (casefolded) is what the
        # real error carries - the pre-Phase-F "requests per day" needle missed it.
        assert classify_failure("429 GenerateRequestsPerDayPerProject") is (
            FailureKind.PROVIDER_RATE_LIMIT
        )


class TestTransientStillTransient:
    def test_gemini_per_minute_stays_rate_limit(self) -> None:
        # A per-MINUTE RESOURCE_EXHAUSTED must NOT disable the provider - it is a
        # short-window limit that a backoff retry can clear.
        assert classify_failure(GEMINI_PER_MINUTE) is FailureKind.RATE_LIMIT
        rec = recovery_for_exception(Exception(GEMINI_PER_MINUTE))
        assert rec.action is Action.RETRY_SAME_WITH_BACKOFF

    def test_plain_429_stays_rate_limit(self) -> None:
        rec = recovery_for("groq: 429 Too Many Requests")
        assert rec.kind is FailureKind.RATE_LIMIT
        assert rec.action is Action.RETRY_SAME_WITH_BACKOFF

    def test_rate_limited_wording_stays_transient(self) -> None:
        assert classify_failure("nvidia: 429 rate limited") is FailureKind.RATE_LIMIT

    def test_resource_exhausted_alone_is_not_daily(self) -> None:
        # RESOURCE_EXHAUSTED without daily wording must not be treated as a daily
        # provider exhaustion (it is not a needle on its own).
        assert classify_failure("429 resource_exhausted too many requests") is (
            FailureKind.RATE_LIMIT
        )


class TestNoFalsePositives:
    def test_unrelated_quota_word_not_daily_exhaustion(self) -> None:
        # A message mentioning "quota" generically (not daily/account exhaustion)
        # must not be misclassified as PROVIDER_RATE_LIMIT.
        assert classify_failure("invalid request: quota field missing") is (FailureKind.UNKNOWN)

    def test_openrouter_daily_unchanged(self) -> None:
        # Existing OpenRouter daily wording still classifies as provider exhaustion.
        assert classify_failure("openrouter: 429 free-models-per-day cap reached") is (
            FailureKind.PROVIDER_RATE_LIMIT
        )


class TestPrecedencePreserved:
    def test_authentication_still_wins(self) -> None:
        assert classify_failure("401 unauthorized: invalid api key") is (FailureKind.AUTHENTICATION)

    def test_timeout_unchanged(self) -> None:
        assert classify_failure("request timed out") is FailureKind.TIMEOUT

    def test_context_limit_unchanged(self) -> None:
        assert classify_failure("error: maximum context length exceeded") is (
            FailureKind.CONTEXT_LIMIT
        )

    def test_insufficient_quota_error_code_still_provider_wide(self) -> None:
        # The structured error_code path (Groq/NVIDIA/OpenAI-style) is unchanged:
        # insufficient_quota remains a provider-wide disable regardless of text.
        from qaops.execution.policy import classify_failure_fields

        assert (
            classify_failure_fields("quota", status_code=429, error_code="insufficient_quota")
            is FailureKind.PROVIDER_RATE_LIMIT
        )
