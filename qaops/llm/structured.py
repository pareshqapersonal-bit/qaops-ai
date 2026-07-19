"""Structured output: the parse -> validate -> retry loop (ADR-002).

generate_structured() is the only way pipeline stages obtain typed data
from a model. It is provider-agnostic (works with any LLMClient),
strict (Pydantic validation with extra="forbid" per ADR-003), and loud:
after exhausting retries it raises LLMResponseFormatError carrying
every raw response, optionally persisting them to disk for debugging.

The retry is a repair loop, not a blind resend: each retry appends the
failed response and the validation error to the conversation so the
model can correct itself.
"""

import json
import logging
import re
import time
from pathlib import Path

from pydantic import BaseModel, ValidationError

from qaops.llm.client import LLMClient
from qaops.llm.errors import LLMResponseFormatError
from qaops.llm.models import LLMRequest

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9]*\s*|\s*```$", flags=re.MULTILINE)


def extract_json_payload(text: str) -> str:
    """Best-effort extraction of a JSON object/array from model output.

    Handles markdown fences and surrounding prose by slicing from the
    first '{' or '[' to the matching last '}' or ']'. Returns the input
    stripped if no JSON delimiters are found (validation will then fail
    with a clear error).
    """
    cleaned = _FENCE_RE.sub("", text).strip()
    starts = [i for i in (cleaned.find("{"), cleaned.find("[")) if i != -1]
    if not starts:
        return cleaned
    start = min(starts)
    end_char = "}" if cleaned[start] == "{" else "]"
    end = cleaned.rfind(end_char)
    if end <= start:
        return cleaned
    return cleaned[start : end + 1]


def generate_structured[T: BaseModel](
    client: LLMClient,
    request: LLMRequest,
    schema: type[T],
    *,
    retries: int = 2,
    failure_dir: Path | None = None,
) -> T:
    """Run a completion and validate the output against a Pydantic schema.

    Args:
        client: any LLMClient implementation.
        request: the initial request. Never mutated; retries build on a
            copy with feedback appended.
        schema: the strict Pydantic model the output must satisfy.
        retries: additional attempts after the first failure (ADR-002
            default: 2, i.e. at most 3 total calls).
        failure_dir: if set, raw responses of a final failure are written
            here as ``<schema>_attempt<N>.txt`` before raising.

    Raises:
        LLMResponseFormatError: if no attempt yields schema-valid output.
        LLMProviderError: propagated unchanged from the client.
    """
    attempts = retries + 1
    raw_responses: list[str] = []
    current = request

    for attempt in range(1, attempts + 1):
        response = client.complete(current)
        raw_responses.append(response.text)
        payload = extract_json_payload(response.text)
        try:
            parsed = json.loads(payload)
            result = schema.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError) as exc:
            logger.warning(
                "structured_output.invalid schema=%s attempt=%d/%d error=%s",
                schema.__name__,
                attempt,
                attempts,
                type(exc).__name__,
            )
            if attempt == attempts:
                _persist_failures(failure_dir, schema.__name__, raw_responses)
                raise LLMResponseFormatError(schema.__name__, attempts, raw_responses) from exc
            current = current.with_feedback(response.text, str(exc))
            continue
        logger.info(
            "structured_output.ok schema=%s attempt=%d/%d", schema.__name__, attempt, attempts
        )
        return result

    raise AssertionError("unreachable")  # pragma: no cover


def _persist_failures(failure_dir: Path | None, schema_name: str, raws: list[str]) -> None:
    if failure_dir is None:
        return
    try:
        failure_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for i, raw in enumerate(raws, start=1):
            (failure_dir / f"{schema_name}_{stamp}_attempt{i}.txt").write_text(raw)
    except OSError:  # debugging aid must never mask the real error
        logger.exception("structured_output.persist_failed dir=%s", failure_dir)
