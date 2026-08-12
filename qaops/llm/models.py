"""Typed request/response models for the LLM boundary.

These models are provider-agnostic: pipeline stages build an LLMRequest
and receive an LLMResponse without knowing which provider served it
(ADR-002). Like the domain models, they are strict - unknown fields are
rejected (ADR-003).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ImagePart(_StrictModel):
    """One image supplied as visual evidence alongside a text message (Phase 36).

    Provider-agnostic. Carries the raw image (base64) plus its media type and
    provenance so a downstream multimodal provider can render it and a reader can
    trace it. In Phase 36 Part 1 no provider consumes images yet; this only defines
    the transport and is attached to the analyzer's request. Provenance fields
    (page/image_index) exist for future embedded-image extraction and default to
    None for standalone images.
    """

    media_type: Literal["image/png", "image/jpeg"] = Field()
    data: str = Field(min_length=1, description="Base64-encoded image bytes.")
    source_filename: str = Field(min_length=1)
    order: int = Field(ge=0, description="Position of this image within the evidence, 0-based.")
    page: int | None = Field(default=None, ge=1)
    image_index: int | None = Field(default=None, ge=0)


class LLMMessage(_StrictModel):
    """One turn in a conversation sent to the model.

    `content` remains the text of the turn. `images` (Phase 36) is an ADDITIVE,
    optional list of visual evidence; it defaults empty, so text-only messages are
    byte-identical to pre-Phase-36 messages under exclude_defaults serialization and
    every existing construction stays valid. Only the RequirementAnalyzer attaches
    images; all other stages leave it empty.
    """

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)
    images: list[ImagePart] = Field(default_factory=list)


class LLMRequest(_StrictModel):
    """A provider-agnostic completion request."""

    system: str = ""
    messages: list[LLMMessage] = Field(min_length=1)
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    max_output_tokens: int = Field(default=8000, ge=256)

    def with_feedback(self, previous_raw: str, error_message: str) -> "LLMRequest":
        """Return a new request appending the failed response and a repair
        instruction. Used by the structured-output retry loop; the original
        request is never mutated."""
        feedback = (
            "Your previous response could not be parsed into the required "
            f"schema. Error:\n{error_message}\n\n"
            "Respond again with ONLY valid JSON matching the schema. "
            "No prose, no markdown fences."
        )
        return LLMRequest(
            system=self.system,
            messages=[
                *self.messages,
                LLMMessage(role="assistant", content=previous_raw or "<empty response>"),
                LLMMessage(role="user", content=feedback),
            ],
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
        )


class LLMUsage(_StrictModel):
    """Token accounting for one completion."""

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMResponse(_StrictModel):
    """A provider-agnostic completion response."""

    text: str
    model: str
    usage: LLMUsage
    stop_reason: str = ""
