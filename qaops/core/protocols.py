"""Structural interfaces that decouple QAOps components.

These are the only contracts future agents and exporters must satisfy.
They are deliberately small: one method each, typed in/out, no shared
state. New capabilities plug in by implementing a protocol, never by
modifying existing modules.
"""

from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

TIn = TypeVar("TIn", bound=BaseModel, contravariant=True)
TOut = TypeVar("TOut", bound=BaseModel, covariant=True)


@runtime_checkable
class PipelineStage(Protocol[TIn, TOut]):
    """One step in a pipeline: consumes one typed model, produces another."""

    @property
    def name(self) -> str:
        """Human-readable stage name used in logs and error messages."""
        ...

    def run(self, data: TIn) -> TOut:
        """Transform the input model into the output model.

        Raises:
            StageError: if the stage cannot produce a valid output.
        """
        ...


@runtime_checkable
class Agent(Protocol[TIn, TOut]):
    """A complete AI capability composed of one or more pipeline stages.

    V1 ships exactly one Agent (Test Design). Future agents (defect
    triage, regression impact, ...) implement this same protocol and
    reuse llm/, models/, exporters/ and config/ unchanged.
    """

    @property
    def name(self) -> str: ...

    def run(self, data: TIn) -> TOut: ...


@runtime_checkable
class Exporter(Protocol):
    """Serializes a TestDesignResult to a file. Independent of generation."""

    @property
    def format_name(self) -> str:
        """Short format identifier, e.g. 'markdown', 'csv', 'xlsx', 'json'."""
        ...

    @property
    def file_extension(self) -> str:
        """File extension including the dot, e.g. '.md'."""
        ...

    def export(self, result: BaseModel, output_path: str) -> str:
        """Write the result to output_path and return the written path."""
        ...
