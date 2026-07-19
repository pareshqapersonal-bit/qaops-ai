"""The LLM boundary: the only package that talks to model providers.

Public API:
- LLMClient (protocol), AnthropicClient, MockLLMClient
- LLMRequest / LLMMessage / LLMResponse / LLMUsage
- generate_structured / extract_json_payload
- PromptLoader
- LLMProviderError / LLMResponseFormatError
"""

from qaops.llm.anthropic_client import AnthropicClient
from qaops.llm.client import LLMClient
from qaops.llm.errors import LLMProviderError, LLMResponseFormatError
from qaops.llm.mock import MockLLMClient
from qaops.llm.models import LLMMessage, LLMRequest, LLMResponse, LLMUsage
from qaops.llm.prompt_loader import PromptLoader
from qaops.llm.structured import extract_json_payload, generate_structured

__all__ = [
    "AnthropicClient",
    "LLMClient",
    "LLMMessage",
    "LLMProviderError",
    "LLMRequest",
    "LLMResponse",
    "LLMResponseFormatError",
    "LLMUsage",
    "MockLLMClient",
    "PromptLoader",
    "extract_json_payload",
    "generate_structured",
]
