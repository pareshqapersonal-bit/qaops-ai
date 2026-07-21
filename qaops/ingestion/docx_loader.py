"""DocxLoader - placeholder (ADR-018).

The extension is registered so dispatch recognizes .docx as a known
format, but extraction is not yet implemented. It raises DocumentLoadError
with a clear "planned, not yet available" message rather than a confusing
"unsupported format" - a recognized-but-unimplemented format is not the
same as an unknown one. Implementing this loader is a drop-in follow-up:
add python-docx extraction here, add the [docx] extra, done. No other
part of QAOps changes.
"""

from pathlib import Path

from qaops.core.errors import DocumentLoadError


class DocxLoader:
    """Placeholder loader for Word .docx documents (not yet implemented)."""

    @property
    def format_name(self) -> str:
        return "DOCX"

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        return (".docx",)

    def load(self, path: Path) -> str:
        msg = (
            "DOCX ingestion is planned but not yet implemented. For now, save the "
            "document as Markdown or plain text and pass that instead."
        )
        raise DocumentLoadError(msg)
