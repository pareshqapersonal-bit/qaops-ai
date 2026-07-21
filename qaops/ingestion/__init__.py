"""Document ingestion: convert supported formats to normalized UTF-8 text.

The third pluggable-format abstraction in QAOps (after providers and
exporters), same shape: a DocumentLoader protocol, concrete loaders,
and an extension registry (ADR-018). `load_document(path)` is the single
entry point; everything downstream receives normalized text and never
learns the source format.

Implemented: text/markdown, PDF. Registered stubs: DOCX, HTML.
"""

from qaops.ingestion.docx_loader import DocxLoader
from qaops.ingestion.html_loader import HtmlLoader
from qaops.ingestion.loader import DocumentLoader
from qaops.ingestion.normalize import normalize_text
from qaops.ingestion.pdf_loader import PdfLoader
from qaops.ingestion.registry import REGISTRY, load_document, supported_extensions
from qaops.ingestion.text_loader import TextLoader

__all__ = [
    "REGISTRY",
    "DocumentLoader",
    "DocxLoader",
    "HtmlLoader",
    "PdfLoader",
    "TextLoader",
    "load_document",
    "normalize_text",
    "supported_extensions",
]
