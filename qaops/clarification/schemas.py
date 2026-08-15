"""LLM wire schemas for the clarification agent (Phase 41B).

These mirror the pipeline's _WireModel convention (extra="forbid") and are the
structured output the LLM returns. They are intentionally small: the agent asks the
model only to shape a gap's question into an answerable form (phrasing + answer type
+ options), not to invent requirements or re-derive severity. Severity -> priority
and requirement linkage are handled deterministically from the existing Gap, so the
model cannot fabricate traceability.
"""

from pydantic import BaseModel, ConfigDict, Field

from qaops.clarification.enums import AnswerType


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShapedQuestion(_WireModel):
    """One gap shaped into an answerable question by the model.

    `gap_index` ties the shaped question back to the gap at that position in the
    prompt's gap list, so the agent can recover the gap's severity/requirement_id
    deterministically (the model never supplies those). `skip` lets the model drop a
    gap whose answer is already present in the source or that is not test-relevant.
    """

    gap_index: int = Field(ge=0)
    skip: bool = False
    question: str = ""
    answer_type: AnswerType = AnswerType.TEXT
    options: list[str] = Field(default_factory=list)
    reason: str = ""


class ShapedQuestionBatch(_WireModel):
    """Top-level clarification-agent output: the shaped questions for a gap set."""

    questions: list[ShapedQuestion] = Field(default_factory=list)
