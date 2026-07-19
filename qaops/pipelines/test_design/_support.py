"""Shared plumbing for LLM-backed stages.

One helper owns the request-building and structured-output call so
every stage stays a thin, single-responsibility class: render prompt,
call, map wire objects to domain models.
"""

from pydantic import BaseModel

from qaops.config import QAOpsSettings
from qaops.llm import LLMClient, LLMMessage, LLMRequest, PromptLoader, generate_structured

SYSTEM_PROMPT = (
    "You are a senior QA engineer with deep experience in requirement "
    "analysis and professional manual test design. You are precise, you "
    "never invent facts that are not grounded in the provided text, and "
    "you respond ONLY with valid JSON matching the requested schema - "
    "no prose, no markdown fences."
)


def run_structured_stage[T: BaseModel](
    *,
    client: LLMClient,
    prompts: PromptLoader,
    settings: QAOpsSettings,
    prompt_name: str,
    schema: type[T],
    **prompt_vars: str,
) -> T:
    """Render a versioned prompt, execute it, and validate the output."""
    rendered = prompts.render(prompt_name, **prompt_vars)
    request = LLMRequest(
        system=SYSTEM_PROMPT,
        messages=[LLMMessage(role="user", content=rendered)],
        temperature=settings.temperature,
        max_output_tokens=settings.max_output_tokens,
    )
    return generate_structured(
        client,
        request,
        schema,
        retries=settings.llm_retries,
        failure_dir=settings.output_dir / "llm_failures",
    )


def requirements_as_prompt_json(requirements: list[BaseModel]) -> str:
    """Serialize domain requirements for inclusion in downstream prompts."""
    return "[" + ",".join(r.model_dump_json(exclude_defaults=True) for r in requirements) + "]"
