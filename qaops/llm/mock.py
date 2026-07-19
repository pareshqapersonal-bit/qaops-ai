"""MockLLMClient - the test double behind the entire unit suite (ADR-008).

Scripted with an ordered queue of responses (plain strings, full
LLMResponse objects, or exceptions to raise). Records every request it
receives so tests can assert on prompts, feedback loops, and call
counts. Raises loudly if called more times than scripted - a test that
under-scripts is a broken test, not a passing one.
"""

from qaops.llm.errors import LLMProviderError
from qaops.llm.models import LLMRequest, LLMResponse, LLMUsage

ScriptedItem = str | LLMResponse | Exception


class MockLLMClient:
    """An LLMClient that replays scripted responses in order."""

    def __init__(self, script: list[ScriptedItem] | None = None, model: str = "mock-model") -> None:
        self._script: list[ScriptedItem] = list(script or [])
        self._model = model
        self.requests: list[LLMRequest] = []

    @property
    def provider_name(self) -> str:
        return "mock"

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._script:
            raise LLMProviderError(
                "mock",
                f"MockLLMClient script exhausted after {len(self.requests) - 1} "
                "call(s); the test made more LLM calls than it scripted.",
            )
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, LLMResponse):
            return item
        return LLMResponse(
            text=item,
            model=self._model,
            usage=LLMUsage(input_tokens=0, output_tokens=0),
            stop_reason="end_turn",
        )
