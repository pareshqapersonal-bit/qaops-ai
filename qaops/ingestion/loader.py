"""The DocumentLoader protocol (ADR-018).

The third pluggable-format abstraction in QAOps, deliberately the same
shape as the Exporter protocol: a structural interface, concrete
implementations in this package, dispatched by an extension registry.

A loader converts a document of one format family into normalized
UTF-8 text. It knows nothing about the pipeline; the pipeline knows
nothing about it. The only contract is: bytes on disk -> normalized text.
"""

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class DocumentLoader(Protocol):
    """Converts a supported document format into normalized UTF-8 text."""

    @property
    def format_name(self) -> str:
        """Human-readable format label, e.g. 'PDF' or 'Markdown'."""
        ...

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        """Lowercased extensions this loader handles, including the dot."""
        ...

    def load(self, path: Path) -> str:
        """Read the document at path and return normalized UTF-8 text.

        Raises:
            DocumentLoadError: if the file cannot be read or its text
                cannot be extracted (including a missing optional
                dependency for the format).
        """
        ...
