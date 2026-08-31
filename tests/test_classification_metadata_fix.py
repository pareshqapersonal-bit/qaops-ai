"""Regression tests for the minimal failure-classification fixes.

Two source-verified fixes:
  1. The NVIDIA client now preserves OpenAI-SDK status_code/error_code metadata
     (like the Groq/OpenRouter clients), so a recognizable NVIDIA 5xx/404/429/auth
     error classifies through the existing status map instead of collapsing to
     UNKNOWN. A genuinely metadata-less error still classifies as UNKNOWN.
  2. HTTP 400 is mapped explicitly (MODEL_UNAVAILABLE) instead of falling through
     to UNKNOWN. No dedicated request-invalid FailureKind exists; MODEL_UNAVAILABLE
     shares UNKNOWN's action/scope, so recovery behaviour is unchanged - only the
     label (and telemetry) becomes honest.

Pure classifier + client-mapping tests; no live calls, no API keys.
"""

from unittest.mock import MagicMock

import pytest
from openai import OpenAIError

from qaops.execution.policy import (
    Action,
    FailureKind,
    classify_failure_fields,
    recovery_for_exception,
)
from qaops.llm.errors import LLMProviderError
from qaops.llm.models import LLMMessage, LLMRequest
from qaops.llm.nvidia_client import NvidiaClient

MODEL = "nvidia/nemotron-nano-12b-v2-vl"


class _StatusError(OpenAIError):
    """An OpenAI-SDK-style error carrying an HTTP status_code (APIStatusError shape)."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _raise_nvidia(exc: BaseException) -> LLMProviderError:
    sdk = MagicMock()
    sdk.chat.completions.create.side_effect = exc
    try:
        NvidiaClient(model=MODEL, sdk_client=sdk).complete(
            LLMRequest(messages=[LLMMessage(role="user", content="x")])
        )
    except LLMProviderError as raised:
        return raised
    raise AssertionError("NvidiaClient did not raise LLMProviderError")


# =====================================================================
# Fix 1 - NVIDIA metadata preservation
# =====================================================================


class TestNvidiaMetadataPreserved:
    def test_nvidia_5xx_classifies_as_timeout_not_unknown(self) -> None:
        err = _raise_nvidia(_StatusError("server exploded", 503))
        assert err.status_code == 503
        rec = recovery_for_exception(err)
        assert rec.kind is FailureKind.TIMEOUT  # was UNKNOWN before the fix
        assert rec.action is Action.RETRY_SAME  # retry, not model exclusion

    def test_nvidia_404_classifies_as_model_unavailable(self) -> None:
        err = _raise_nvidia(_StatusError("no such model", 404))
        assert err.status_code == 404
        assert recovery_for_exception(err).kind is FailureKind.MODEL_UNAVAILABLE

    def test_nvidia_429_classifies_as_rate_limit(self) -> None:
        err = _raise_nvidia(_StatusError("slow down", 429))
        assert err.status_code == 429
        rec = recovery_for_exception(err)
        assert rec.kind is FailureKind.RATE_LIMIT
        assert rec.action is Action.RETRY_SAME_WITH_BACKOFF

    def test_nvidia_auth_classifies_as_authentication(self) -> None:
        err = _raise_nvidia(_StatusError("bad key", 401))
        assert err.status_code == 401
        rec = recovery_for_exception(err)
        assert rec.kind is FailureKind.AUTHENTICATION
        assert rec.action is Action.DISABLE_AND_SWITCH  # run-sticky, unchanged

    def test_nvidia_opaque_error_without_metadata_stays_unknown(self) -> None:
        # The important guard: we must NOT pretend metadata exists. A plain
        # OpenAIError with no status_code still classifies as UNKNOWN.
        err = _raise_nvidia(OpenAIError("totally opaque, no status"))
        assert err.status_code is None
        assert recovery_for_exception(err).kind is FailureKind.UNKNOWN


# =====================================================================
# Fix 2 - HTTP 400 no longer silently UNKNOWN
# =====================================================================


class TestHttp400:
    def test_400_is_not_unknown(self) -> None:
        kind = classify_failure_fields("bad request payload", status_code=400)
        assert kind is not FailureKind.UNKNOWN

    def test_400_maps_to_model_unavailable(self) -> None:
        # Closest honest label given no request-invalid kind; shares UNKNOWN's
        # action/scope so recovery behaviour is unchanged.
        assert classify_failure_fields("bad request", status_code=400) is (
            FailureKind.MODEL_UNAVAILABLE
        )

    def test_400_action_and_scope_unchanged_vs_unknown(self) -> None:
        # MODEL_UNAVAILABLE and UNKNOWN both drop the model, stage-local: recovery
        # behaviour is identical, only the classification label differs.
        from qaops.execution.policy import _POLICY

        m = _POLICY[FailureKind.MODEL_UNAVAILABLE]
        u = _POLICY[FailureKind.UNKNOWN]
        assert m.disables_model == u.disables_model is True
        assert m.disables_provider == u.disables_provider is False


# =====================================================================
# Guardrails - other statuses unchanged (no regression)
# =====================================================================


class TestOtherStatusesUnchanged:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (401, FailureKind.AUTHENTICATION),
            (402, FailureKind.INSUFFICIENT_CREDIT),
            (404, FailureKind.MODEL_UNAVAILABLE),
            (408, FailureKind.TIMEOUT),
            (429, FailureKind.RATE_LIMIT),
            (500, FailureKind.TIMEOUT),
            (503, FailureKind.TIMEOUT),
        ],
    )
    def test_status_map_unchanged(self, status: int, expected: FailureKind) -> None:
        assert classify_failure_fields("body", status_code=status) is expected
