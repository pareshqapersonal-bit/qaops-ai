"""Phase 41C-3 regression tests: clarification client lifecycle.

Confirms the fix for the shared-AsyncOpenAI/event-loop bug: the clarification path
must build a FRESH client per LLM call (analyzer, gap, agent) rather than reuse one
across run_with_deadline's per-call asyncio loops. Reusing a single provider client
(whose httpx pool binds to the first loop) across those closed loops produced
"[groq] Connection error" on the second call. These tests prove a distinct client
backs each call, while all existing 41C behavior/contracts are preserved.
"""

import json
from pathlib import Path

import pytest

from qaops.clarification.enums import AnswerType, ClarificationStatus
from qaops.clarification.models import ClarificationAnswer
from qaops.clarification.service import ClarificationService
from qaops.config import QAOpsSettings
from qaops.llm import MockLLMClient

_ANALYZER = json.dumps(
    {
        "requirements": [
            {
                "title": "Store availability",
                "description": "User checks store availability by pincode.",
                "source_excerpt": "check store availability",
            }
        ]
    }
)
_GAP_BLOCKER = json.dumps(
    {
        "gaps": [
            {
                "description": "Retry on API failure undefined",
                "severity": "blocker",
                "requirement_id": "REQ-001",
                "suggested_question": "Retry?",
            }
        ]
    }
)
_AGENT_ONE = json.dumps(
    {
        "questions": [
            {
                "gap_index": 0,
                "skip": False,
                "question": "Allow retry when the API fails?",
                "answer_type": "boolean",
                "options": [],
                "reason": "coverage",
            }
        ]
    }
)


@pytest.fixture(autouse=True)
def _keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


def _workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "run_1"
    (ws / "input").mkdir(parents=True)
    (ws / "output").mkdir(parents=True)
    (ws / "input" / "ticket.md").write_text("User checks store availability by pincode.")
    return ws


class _ScriptedFactory:
    """Hands out a fresh single-response MockLLMClient per create_client call.

    Mirrors production, where each stage builds its own client. Records every
    client so the test can assert distinct instances and one call each - i.e. no
    client is reused across the separate event loops run_with_deadline creates.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.clients: list[MockLLMClient] = []
        self._i = 0

    def __call__(self, _settings: object) -> MockLLMClient:
        # Each fresh client is scripted with exactly the one response its single
        # call consumes, in analyzer -> gap -> agent order.
        response = self._responses[self._i]
        self._i += 1
        client = MockLLMClient([response])
        self.clients.append(client)
        return client


def _clarify_patch(side_effect):
    """Patch the resilient-call seam (Phase 41C-4) so each attempt builds its
    client via the given factory, with a single nvidia candidate."""
    from contextlib import ExitStack
    from unittest.mock import patch as _patch

    from qaops.execution.registry import get_provider

    stack = ExitStack()
    stack.enter_context(
        _patch("qaops.execution.resilient_call.create_client", side_effect=side_effect)
    )
    stack.enter_context(
        _patch(
            "qaops.execution.resilient_call.fallback_providers",
            return_value=[get_provider("nvidia")],
        )
    )
    return stack


class TestClientLifecycle:
    def test_fresh_client_per_llm_call(self, tmp_path: Path) -> None:
        # The core regression: analyzer, gap, and agent each get their OWN client.
        ws = _workspace(tmp_path)
        factory = _ScriptedFactory([_ANALYZER, _GAP_BLOCKER, _AGENT_ONE])
        svc = ClarificationService(QAOpsSettings(output_dir=ws / "output"))
        with _clarify_patch(factory):
            state = svc.start("run_1", ws / "input" / "ticket.md", ws)

        # Three separate clients were constructed (one per LLM call).
        assert len(factory.clients) == 3
        # They are distinct instances - no client reused across calls/loops.
        assert len({id(c) for c in factory.clients}) == 3
        # Each client made exactly one request (never a second call on a client
        # whose transport is bound to a closed loop).
        assert [c.call_count for c in factory.clients] == [1, 1, 1]
        # And the workflow still produced the expected question batch.
        assert len(state.questions) == 1
        assert state.status is ClarificationStatus.CLARIFYING

    def test_no_client_reused_across_calls(self, tmp_path: Path) -> None:
        # Explicitly assert the pre-fix pattern (one client, three calls) is gone:
        # if any single client had served two calls, its call_count would be > 1.
        ws = _workspace(tmp_path)
        factory = _ScriptedFactory([_ANALYZER, _GAP_BLOCKER, _AGENT_ONE])
        svc = ClarificationService(QAOpsSettings(output_dir=ws / "output"))
        with _clarify_patch(factory):
            svc.start("run_1", ws / "input" / "ticket.md", ws)
        assert all(c.call_count == 1 for c in factory.clients)

    def test_provider_and_output_tokens_unchanged(self, tmp_path: Path) -> None:
        # The fix changes only client lifecycle - provider selection and
        # max_output_tokens still come straight from settings, untouched.
        ws = _workspace(tmp_path)
        settings = QAOpsSettings(output_dir=ws / "output")
        assert settings.max_output_tokens == 8000  # default; prod overrides to 4000

        captured: dict = {}

        class _Spy(MockLLMClient):
            def complete(self, request):  # type: ignore[override]
                captured.setdefault("max_output_tokens", request.max_output_tokens)
                captured.setdefault("images", request.messages[0].images)
                return super().complete(request)

        factory_responses = [_ANALYZER, _GAP_BLOCKER, _AGENT_ONE]
        idx = {"i": 0}

        def _factory(_s: object) -> _Spy:
            c = _Spy([factory_responses[idx["i"]]])
            idx["i"] += 1
            return c

        svc = ClarificationService(settings)
        with _clarify_patch(_factory):
            svc.start("run_1", ws / "input" / "ticket.md", ws)

        # Requests carry settings.max_output_tokens verbatim (no 32768 anywhere).
        assert captured["max_output_tokens"] == settings.max_output_tokens
        # The analyzer's first request carries no images for a text PRD.
        assert captured["images"] == []


class TestBehaviorPreserved:
    def test_full_flow_still_reaches_ready(self, tmp_path: Path) -> None:
        # End-to-end 41C behavior is unchanged: answer the blocker -> READY.
        ws = _workspace(tmp_path)
        factory = _ScriptedFactory([_ANALYZER, _GAP_BLOCKER, _AGENT_ONE])
        svc = ClarificationService(QAOpsSettings(output_dir=ws / "output"))
        with _clarify_patch(factory):
            state = svc.start("run_1", ws / "input" / "ticket.md", ws)
        ans = [
            ClarificationAnswer(
                question_id=state.questions[0].question_id,
                answer_type=AnswerType.BOOLEAN,
                answer="true",
            )
        ]
        # submit_answers is client-free (pure) - no create_client needed here.
        new_state = svc.submit_answers(ws, ans)
        assert new_state.readiness.ready is True
        assert new_state.status is ClarificationStatus.READY_FOR_TEST_DESIGN

    def test_no_images_passed_to_agent(self, tmp_path: Path) -> None:
        # The clarification agent call is text-only (41B invariant preserved).
        ws = _workspace(tmp_path)
        seen: list = []

        class _Spy(MockLLMClient):
            def complete(self, request):  # type: ignore[override]
                seen.append(request.messages[0].images)
                return super().complete(request)

        responses = [_ANALYZER, _GAP_BLOCKER, _AGENT_ONE]
        idx = {"i": 0}

        def _factory(_s: object) -> _Spy:
            c = _Spy([responses[idx["i"]]])
            idx["i"] += 1
            return c

        svc = ClarificationService(QAOpsSettings(output_dir=ws / "output"))
        with _clarify_patch(_factory):
            svc.start("run_1", ws / "input" / "ticket.md", ws)
        # Every clarification-path request (incl. the agent, the 3rd) carries no images.
        assert all(imgs == [] for imgs in seen)
