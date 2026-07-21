"""PdfLoader - PDF text extraction (implemented).

Extracts linear text from each page and joins with blank lines, then
normalizes. It does NOT attempt layout reconstruction (columns, tables,
reading order) - that is a quality rabbit hole deferred behind this same
interface (ADR-018). A scanned, image-only PDF yields no extractable
text; rather than run the pipeline on emptiness, the loader raises a
clear error pointing at the real cause (no text layer / OCR needed).

pypdf ships as the optional [pdf] extra; a missing install raises a
friendly DocumentLoadError naming the install command.
"""

from pathlib import Path

from qaops.core.errors import DocumentLoadError
from qaops.ingestion.normalize import normalize_text


class PdfLoader:
    """Loads PDF documents by extracting their text layer."""

    @property
    def format_name(self) -> str:
        return "PDF"

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        return (".pdf",)

    def load(self, path: Path) -> str:
        try:
            from pypdf import PdfReader
            from pypdf.errors import PyPdfError
        except ImportError as exc:
            msg = "PDF support requires pypdf. Install it with: pip install 'qaops-ai[pdf]'."
            raise DocumentLoadError(msg) from exc

        try:
            reader = PdfReader(str(path))
            pages = [page.extract_text() or "" for page in reader.pages]
        except (PyPdfError, OSError, ValueError) as exc:
            msg = f"Could not read PDF {path}: {exc}"
            raise DocumentLoadError(msg) from exc

        combined = "\n\n".join(pages)
        normalized = normalize_text(combined)
        if not normalized:
            msg = (
                f"No extractable text found in {path}. It may be a scanned or "
                "image-only PDF with no text layer; OCR is not supported."
            )
            raise DocumentLoadError(msg)
        return normalized
