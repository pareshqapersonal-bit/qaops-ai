"""Extension-to-loader registry and the load_document dispatcher (ADR-018).

The same pattern as the exporter registry: a plain dict mapping file
extensions to loader instances - no if/else chain. load_document is the
single entry point callers use instead of read_text; it resolves the
extension, dispatches to the loader, and raises a friendly
UnsupportedDocumentFormatError (listing supported formats and an install
hint) for anything unmapped.
"""

from pathlib import Path

from qaops.core.errors import UnsupportedDocumentFormatError
from qaops.ingestion.docx_loader import DocxLoader
from qaops.ingestion.html_loader import HtmlLoader
from qaops.ingestion.loader import DocumentLoader
from qaops.ingestion.pdf_loader import PdfLoader
from qaops.ingestion.text_loader import TextLoader

_TEXT = TextLoader()
_PDF = PdfLoader()
_DOCX = DocxLoader()
_HTML = HtmlLoader()

# Extension -> loader instance. Stub formats (docx, html) are registered so
# dispatch recognizes them as known-but-unimplemented (their loaders raise a
# clear message) rather than surfacing them as unknown formats.
REGISTRY: dict[str, DocumentLoader] = {
    ".txt": _TEXT,
    ".md": _TEXT,
    ".markdown": _TEXT,
    ".pdf": _PDF,
    ".docx": _DOCX,
    ".html": _HTML,
    ".htm": _HTML,
}

# Extensions whose loaders need an optional extra, for the install hint.
_INSTALL_HINTS: dict[str, str] = {
    ".pdf": "Install PDF support with: pip install 'qaops-ai[pdf]'.",
}


def supported_extensions() -> list[str]:
    """All registered extensions, sorted, for user-facing messages."""
    return sorted(REGISTRY)


def load_document(path: Path) -> str:
    """Load any registered document format as normalized UTF-8 text.

    This is the single ingestion entry point. Downstream code receives
    only the returned text and never learns the source format.

    Raises:
        UnsupportedDocumentFormatError: extension has no registered loader.
        DocumentLoadError: the file cannot be read or extracted (including
            a missing optional dependency, or a registered-but-unimplemented
            stub format).
    """
    extension = path.suffix.lower()
    loader = REGISTRY.get(extension)
    if loader is None:
        raise UnsupportedDocumentFormatError(
            extension=extension or "(no extension)",
            supported=supported_extensions(),
            install_hint=_INSTALL_HINTS.get(extension, ""),
        )
    return loader.load(path)
