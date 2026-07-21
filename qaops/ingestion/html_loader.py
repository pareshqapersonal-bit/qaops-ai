"""HtmlLoader - placeholder (ADR-018).

The extensions are registered so dispatch recognizes .html/.htm as a known
format, but text extraction is not yet implemented. Raises DocumentLoadError
with a clear "planned, not yet available" message. Implementing this loader
is a drop-in follow-up: strip tags to text here (stdlib html.parser, no new
dependency), done. No other part of QAOps changes.
"""

from pathlib import Path

from qaops.core.errors import DocumentLoadError


class HtmlLoader:
    """Placeholder loader for HTML documents (not yet implemented)."""

    @property
    def format_name(self) -> str:
        return "HTML"

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        return (".html", ".htm")

    def load(self, path: Path) -> str:
        msg = (
            "HTML ingestion is planned but not yet implemented. For now, save the "
            "document as Markdown or plain text and pass that instead."
        )
        raise DocumentLoadError(msg)
