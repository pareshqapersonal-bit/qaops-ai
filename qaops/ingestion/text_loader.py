"""TextLoader - plain text and Markdown (implemented).

Preserves the pre-ingestion behavior (UTF-8 read) but routes the result
through the shared normalization contract, so text/markdown input is now
BOM-stripped and LF-normalized like every other format.
"""

from pathlib import Path

from qaops.core.errors import DocumentLoadError
from qaops.ingestion.normalize import normalize_text


class TextLoader:
    """Loads plain-text and Markdown documents."""

    @property
    def format_name(self) -> str:
        return "Text/Markdown"

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        return (".txt", ".md", ".markdown")

    def load(self, path: Path) -> str:
        try:
            raw = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            msg = (
                f"{path} is not valid UTF-8 text. If this is a binary document "
                "(PDF, DOCX), use the matching format extension."
            )
            raise DocumentLoadError(msg) from exc
        except OSError as exc:
            msg = f"Could not read {path}: {exc}"
            raise DocumentLoadError(msg) from exc
        return normalize_text(raw)
