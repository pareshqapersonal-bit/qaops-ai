"""The LLMClient protocol - the single LLM boundary (ADR-002).

Every model call in QAOps goes through this interface. Pipeline stages
receive an LLMClient by constructor injection and never import a
provider SDK directly.
"""

from typing import Protocol, runtime_checkable

from qaops.llm.models import LLMRequest, LLMResponse


@runtime_checkable
class LLMClient(Protocol):
    """A synchronous completion client for one provider."""

    @property
    def provider_name(self) -> str:
        """Short provider identifier, e.g. 'anthropic' or 'mock'."""
        ...

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Execute one completion.

        Raises:
            LLMProviderError: on any provider-side failure.
        """
        ...
