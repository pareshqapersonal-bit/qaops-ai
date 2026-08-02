"""DocxLoader - Word .docx text extraction (implemented, ADR-018/ADR-039).

Extracts a linear, readable text rendering of a .docx document and routes it
through the shared normalization contract, so downstream stages receive the
same normalized-text model they get from PDF or Markdown and never learn the
source format.

Structure preservation (Phase 24, Step 5). python-docx exposes the body as an
ordered stream of block items - paragraphs and tables. We walk that stream in
document order and render each block to text:

- Headings -> Markdown ATX headings ("# ", "## ", ...), depth from the
  paragraph's outline level, so the requirement analyzer sees the same heading
  cues it already gets from Markdown input.
- Title -> a top-level "# " heading.
- Numbered list items -> "1. " lines; bullet list items -> "- " lines. Word
  stores list membership in the paragraph style / numbering, not the text, so we
  detect it from the style name and render an explicit marker.
- Plain paragraphs -> their text.
- Tables -> flattened to pipe-delimited rows with a header separator (Step 5
  "flatten if necessary"), each cell's text joined, so tabular requirements
  survive as readable text rather than being dropped.
- Core metadata (title/subject/author) -> not injected into the body; the title
  is emitted as the leading heading when present so it participates in analysis.

The document order matters: interleaving paragraphs and tables as they appear
keeps sections and their tables together. An empty document (no extractable
text) raises DocumentLoadError, mirroring the PDF loader, rather than running
the pipeline on emptiness. A corrupt/unreadable file raises DocumentLoadError
with the underlying cause.

python-docx ships as the optional [docx] extra; a missing install raises a
friendly DocumentLoadError naming the install command.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from qaops.core.errors import DocumentLoadError
from qaops.ingestion.normalize import normalize_text

if TYPE_CHECKING:
    from docx.document import Document as _DocxDocument
    from docx.table import Table as _DocxTable
    from docx.text.paragraph import Paragraph as _DocxParagraph


class DocxLoader:
    """Loads Word .docx documents by rendering their body to normalized text."""

    @property
    def format_name(self) -> str:
        return "DOCX"

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        return (".docx",)

    def load(self, path: Path) -> str:
        try:
            import docx
            from docx.opc.exceptions import PackageNotFoundError
        except ImportError as exc:
            msg = (
                "DOCX support requires python-docx. Install it with: pip install 'qaops-ai[docx]'."
            )
            raise DocumentLoadError(msg) from exc

        try:
            document = docx.Document(str(path))
        except PackageNotFoundError as exc:
            # python-docx raises this for a missing file OR a file that is not a
            # valid .docx package (e.g. a renamed .doc, an image, a zip).
            msg = (
                f"Could not read DOCX {path}: it is missing or not a valid Word "
                ".docx file. Legacy .doc is not supported; re-save as .docx."
            )
            raise DocumentLoadError(msg) from exc
        except (OSError, ValueError, KeyError) as exc:
            msg = f"Could not read DOCX {path}: {exc}"
            raise DocumentLoadError(msg) from exc

        lines = self._render_body(document)
        combined = "\n\n".join(lines)
        normalized = normalize_text(combined)
        if not normalized:
            msg = (
                f"No extractable text found in {path}. The document appears to be "
                "empty or contains only non-text content."
            )
            raise DocumentLoadError(msg)
        return normalized

    # --- rendering -----------------------------------------------------------

    def _render_body(self, document: "_DocxDocument") -> list[str]:
        """Render the body's block stream (paragraphs + tables) in order."""
        blocks: list[str] = []

        title = self._document_title(document)
        if title:
            blocks.append(f"# {title}")

        for block in self._iter_block_items(document):
            if _is_paragraph(block):
                rendered = self._render_paragraph(block)
            else:
                rendered = self._render_table(block)
            if rendered:
                blocks.append(rendered)
        return blocks

    def _document_title(self, document: "_DocxDocument") -> str:
        try:
            title = document.core_properties.title
        except (AttributeError, ValueError):
            return ""
        return (title or "").strip()

    def _render_paragraph(self, para: "_DocxParagraph") -> str:
        text = para.text.strip()
        if not text:
            return ""
        style = (para.style.name if para.style is not None else "") or ""
        style_l = style.casefold()

        # Title paragraph style -> top-level heading.
        if style_l == "title":
            return f"# {text}"

        # Heading N -> ATX heading at that depth (cap at 6).
        heading_level = self._heading_level(style_l)
        if heading_level is not None:
            hashes = "#" * min(max(heading_level, 1), 6)
            return f"{hashes} {text}"

        # List items: Word encodes these via style ("List Bullet", "List
        # Number", "List Paragraph") and/or numbering. Render an explicit marker.
        marker = self._list_marker(para, style_l)
        if marker is not None:
            return f"{marker} {text}"

        return text

    def _render_table(self, table: "_DocxTable") -> str:
        """Flatten a table to pipe-delimited rows (Step 5 'flatten if necessary').

        The first row is treated as a header with a separator line, which reads
        as a Markdown table and keeps columns aligned with their values for the
        analyzer. Empty tables render to nothing.
        """
        rows: list[list[str]] = []
        for row in table.rows:
            cells = [" ".join(cell.text.split()) for cell in row.cells]
            if any(cells):
                rows.append(cells)
        if not rows:
            return ""

        width = max(len(r) for r in rows)
        rendered_rows = []
        for i, row in enumerate(rows):
            padded = row + [""] * (width - len(row))
            rendered_rows.append("| " + " | ".join(padded) + " |")
            if i == 0:
                rendered_rows.append("| " + " | ".join(["---"] * width) + " |")
        return "\n".join(rendered_rows)

    # --- structure detection helpers -----------------------------------------

    @staticmethod
    def _heading_level(style_lower: str) -> int | None:
        # "heading 1".."heading 9"
        prefix = "heading "
        if style_lower.startswith(prefix):
            tail = style_lower[len(prefix) :].strip()
            if tail.isdigit():
                return int(tail)
        return None

    @staticmethod
    def _list_marker(para: "_DocxParagraph", style_lower: str) -> str | None:
        """Return "-" for bullets, "1." for numbered items, else None.

        Detection is style-name based (robust and dependency-light): "List
        Bullet" -> bullet; "List Number" -> numbered. A bare "List Paragraph"
        (Word's generic list style) is treated as a bullet, the safe default, if
        it also carries numbering; otherwise it is left as a plain paragraph so
        ordinary indented text is not mislabelled.
        """
        if "list bullet" in style_lower:
            return "-"
        if "list number" in style_lower:
            return "1."
        if "list paragraph" in style_lower and _has_numbering(para):
            return "-"
        return None

    @staticmethod
    def _iter_block_items(document: "_DocxDocument") -> "list[Any]":
        """Yield paragraphs and tables in document order.

        python-docx has no public in-order iterator over mixed block items, so we
        walk the body element's XML children and map each to its paragraph/table
        wrapper - the documented approach for order-preserving extraction.
        """
        from docx.oxml.table import CT_Tbl
        from docx.oxml.text.paragraph import CT_P
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        parent = document.element.body
        items: list[Any] = []
        for child in parent.iterchildren():
            if isinstance(child, CT_P):
                items.append(Paragraph(child, document))
            elif isinstance(child, CT_Tbl):
                items.append(Table(child, document))
        return items


def _is_paragraph(block: Any) -> bool:
    from docx.text.paragraph import Paragraph

    return isinstance(block, Paragraph)


def _has_numbering(para: "_DocxParagraph") -> bool:
    """True if the paragraph carries list numbering in its properties."""
    try:
        p_pr = para._p.pPr  # noqa: SLF001 - python-docx exposes structure only here
        return p_pr is not None and p_pr.numPr is not None
    except AttributeError:
        return False
