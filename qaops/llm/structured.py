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
from qaops.llm.errors import LLMEmptyResponseError, LLMProviderError, LLMResponseFormatError
from qaops.llm.models import LLMRequest
from qaops.llm.request_budget import NullRequestObserver, RequestObserver

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
    observer: RequestObserver | None = None,
) -> T:
    """Run a completion and validate the output against a Pydantic schema.

    The retry here is a deterministic *repair* loop: when a response is
    substantial but malformed, the next attempt appends the failed response and
    the validation error so the model can correct itself. It is NOT a blind
    resend, and it stops early when a repair cannot help (an empty response has
    nothing to repair).

    Every real provider call is announced to `observer` before it happens, so
    the execution layer can count it and, when its budget is spent, veto further
    calls by raising RequestBudgetExhausted (ADR-030). This is the seam that
    keeps one actual provider call equal to one counted request.

    Args:
        client: any LLMClient implementation.
        request: the initial request. Never mutated; repairs build on a copy
            with feedback appended.
        schema: the strict Pydantic model the output must satisfy.
        retries: additional repair attempts after the first failure (ADR-002
            default: 2, i.e. at most 3 total calls) - an upper bound; an empty
            response ends the loop sooner.
        failure_dir: if set, raw responses of a final failure are written here.
        observer: notified around each provider call; may veto further calls.

    Raises:
        LLMResponseFormatError: if no attempt yields schema-valid output.
        LLMEmptyResponseError: if the provider returned no content.
        LLMProviderError: propagated unchanged from the client.
        RequestBudgetExhausted: propagated from the observer.
    """
    obs = observer or NullRequestObserver()

    # Phase 36: never silently discard visual evidence. If the request carries
    # images but the provider does not declare image support, fail clearly rather
    # than sending a request whose images would be dropped. Providers default to
    # text-only (supports_images is absent/False) until a multimodal provider is
    # added in a later, separately-approved phase.
    if any(m.images for m in request.messages) and not getattr(client, "supports_images", False):
        raise LLMProviderError(
            getattr(client, "provider_name", "unknown"),
            "This request includes image evidence, but the configured provider/model "
            "does not support image input. Select a multimodal provider/model, or "
            "submit the run without visual evidence.",
        )

    attempts = retries + 1
    raw_responses: list[str] = []
    current = request

    for attempt in range(1, attempts + 1):
        # Announce the call first; the observer may forbid it (budget spent).
        obs.before_request(provider=client.provider_name, model=client.model, attempt=attempt)
        response = client.complete(current)
        raw_responses.append(response.text)
        chars = len(response.text.strip())
        is_empty = chars == 0
        obs.after_request(
            provider=client.provider_name,
            model=client.model,
            attempt=attempt,
            empty=is_empty,
            chars=chars,
        )

        # An empty response is a distinct failure (ADR-030). A repair prompt
        # cannot fix "nothing" - re-rolling the same empty-returning model just
        # burns provider calls - so we stop the loop and fail with a dedicated
        # error the executor classifies as EMPTY_OUTPUT (-> next model).
        if is_empty:
            logger.warning(
                "structured_output.empty_response schema=%s attempt=%d/%d "
                "provider=%s model=%s stop_reason=%s (no content returned; not "
                "a token-cap truncation - check model availability, rate "
                "limits, or free-tier capacity)",
                schema.__name__,
                attempt,
                attempts,
                client.provider_name,
                response.model,
                response.stop_reason,
            )
            _persist_failures(failure_dir, schema.__name__, raw_responses)
            raise LLMEmptyResponseError(
                schema.__name__, attempt, client.provider_name, response.model
            )

        # Truncation is only meaningful when there IS content that was cut off.
        # stop_reason=length with zero characters is NOT evidence that the
        # output token cap truncated useful output (handled above as empty); it
        # is a provider/model failure. Only flag truncation for non-empty output.
        truncated = not is_empty and response.stop_reason in {"length", "max_tokens", "MAX_TOKENS"}
        if truncated:
            logger.warning(
                "structured_output.truncated schema=%s attempt=%d/%d "
                "stop_reason=%s chars=%d (output hit the token cap; raise "
                "max_output_tokens in qaops.yaml)",
                schema.__name__,
                attempt,
                attempts,
                response.stop_reason,
                chars,
            )

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
                raise LLMResponseFormatError(
                    schema.__name__, attempts, raw_responses, truncated=truncated
                ) from exc
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
            # Explicit UTF-8: the platform default is cp1252 on Windows, which
            # cannot encode characters such as U+2265 that routinely appear in
            # model output, and would crash here instead of surfacing the real
            # schema failure.
            (failure_dir / f"{schema_name}_{stamp}_attempt{i}.txt").write_text(
                raw, encoding="utf-8"
            )
    except Exception:  # debugging aid must never mask the real error
        logger.exception("structured_output.persist_failed dir=%s", failure_dir)
