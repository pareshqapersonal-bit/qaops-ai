"""Phase 24 tests: DOCX ingestion (ADR-039).

Covers the DocxLoader end to end - heading, paragraph, numbered/bullet list, and
table extraction; mixed formatting; empty and malformed documents; registry
dispatch; the install-hint plumbing; and the key regression guarantee that the
same requirement content stored as DOCX and as Markdown normalizes to
byte-identical text, so the ingestion layer delivers identical pipeline input
regardless of source format. (Whether the final artifacts are identical then
depends on provider/model determinism, not on ingestion.) No LLM calls anywhere
here.
"""

from pathlib import Path

import pytest

from qaops.core.errors import DocumentLoadError
from qaops.ingestion import load_document
from qaops.ingestion.docx_loader import DocxLoader

docx = pytest.importorskip("docx")


def _doc():  # type: ignore[no-untyped-def]
    return docx.Document()


def _save(document, path: Path) -> Path:  # type: ignore[no-untyped-def]
    document.save(str(path))
    return path


class TestHeadings:
    def test_title_and_headings_become_atx(self, tmp_path: Path) -> None:
        d = _doc()
        d.core_properties.title = "Login Spec"
        d.add_heading("Overview", level=1)
        d.add_heading("Detail", level=2)
        d.add_heading("Edge", level=3)
        out = load_document(_save(d, tmp_path / "h.docx"))
        assert "# Login Spec" in out
        assert "# Overview" in out
        assert "## Detail" in out
        assert "### Edge" in out

    def test_title_paragraph_style_is_top_heading(self, tmp_path: Path) -> None:
        d = _doc()
        d.add_paragraph("Requirements Document", style="Title")
        d.add_paragraph("Body text.")
        out = load_document(_save(d, tmp_path / "t.docx"))
        assert out.startswith("# Requirements Document")


class TestParagraphs:
    def test_plain_paragraphs_preserved(self, tmp_path: Path) -> None:
        d = _doc()
        d.add_paragraph("The system shall authenticate the user.")
        d.add_paragraph("The session expires after inactivity.")
        out = load_document(_save(d, tmp_path / "p.docx"))
        assert "The system shall authenticate the user." in out
        assert "The session expires after inactivity." in out

    def test_blank_paragraphs_do_not_create_noise(self, tmp_path: Path) -> None:
        d = _doc()
        d.add_paragraph("First.")
        d.add_paragraph("")
        d.add_paragraph("   ")
        d.add_paragraph("Second.")
        out = load_document(_save(d, tmp_path / "b.docx"))
        # normalization collapses blank runs; no triple newlines survive
        assert "\n\n\n" not in out
        assert "First." in out and "Second." in out


class TestNumberedLists:
    def test_numbered_items_rendered(self, tmp_path: Path) -> None:
        d = _doc()
        d.add_paragraph("Step one", style="List Number")
        d.add_paragraph("Step two", style="List Number")
        out = load_document(_save(d, tmp_path / "n.docx"))
        assert "1. Step one" in out
        assert "1. Step two" in out


class TestBulletLists:
    def test_bullet_items_rendered(self, tmp_path: Path) -> None:
        d = _doc()
        d.add_paragraph("Alpha", style="List Bullet")
        d.add_paragraph("Beta", style="List Bullet")
        out = load_document(_save(d, tmp_path / "bl.docx"))
        assert "- Alpha" in out
        assert "- Beta" in out


class TestTables:
    def test_table_flattened_to_pipe_rows(self, tmp_path: Path) -> None:
        d = _doc()
        t = d.add_table(rows=2, cols=2)
        t.rows[0].cells[0].text = "Field"
        t.rows[0].cells[1].text = "Constraint"
        t.rows[1].cells[0].text = "Password"
        t.rows[1].cells[1].text = "At least 8 characters"
        out = load_document(_save(d, tmp_path / "tbl.docx"))
        assert "| Field | Constraint |" in out
        assert "| --- | --- |" in out
        assert "| Password | At least 8 characters |" in out

    def test_table_and_paragraphs_kept_in_document_order(self, tmp_path: Path) -> None:
        d = _doc()
        d.add_heading("Section A", level=1)
        d.add_paragraph("Intro before table.")
        t = d.add_table(rows=1, cols=1)
        t.rows[0].cells[0].text = "Cell value"
        d.add_paragraph("Text after table.")
        out = load_document(_save(d, tmp_path / "ord.docx"))
        # order preserved: heading, intro, table, trailing text
        assert out.index("Section A") < out.index("Intro before table.")
        assert out.index("Intro before table.") < out.index("Cell value")
        assert out.index("Cell value") < out.index("Text after table.")


class TestMixedFormatting:
    def test_mixed_document_extracts_all_structures(self, tmp_path: Path) -> None:
        d = _doc()
        d.core_properties.title = "Checkout Requirements"
        d.add_heading("Overview", level=1)
        d.add_paragraph("The system shall calculate the order total.")
        d.add_heading("Rules", level=2)
        d.add_paragraph("Compute subtotal", style="List Number")
        d.add_paragraph("Apply tax", style="List Number")
        d.add_paragraph("Free shipping over threshold", style="List Bullet")
        t = d.add_table(rows=2, cols=2)
        t.rows[0].cells[0].text = "Tier"
        t.rows[0].cells[1].text = "Discount"
        t.rows[1].cells[0].text = "Gold"
        t.rows[1].cells[1].text = "10%"
        out = load_document(_save(d, tmp_path / "mix.docx"))
        for expected in (
            "# Checkout Requirements",
            "# Overview",
            "## Rules",
            "1. Compute subtotal",
            "- Free shipping over threshold",
            "| Tier | Discount |",
            "| Gold | 10% |",
        ):
            assert expected in out, expected


class TestEmptyAndMalformed:
    def test_empty_document_raises(self, tmp_path: Path) -> None:
        d = _doc()
        with pytest.raises(DocumentLoadError, match="No extractable text"):
            load_document(_save(d, tmp_path / "empty.docx"))

    def test_malformed_docx_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.docx"
        f.write_bytes(b"not a real docx package")
        with pytest.raises(DocumentLoadError, match="not a valid Word"):
            load_document(f)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(DocumentLoadError):
            DocxLoader().load(tmp_path / "does-not-exist.docx")


class TestRegistryAndHints:
    def test_docx_dispatches_through_load_document(self, tmp_path: Path) -> None:
        d = _doc()
        d.add_paragraph("A requirement.")
        out = load_document(_save(d, tmp_path / "r.docx"))
        assert "A requirement." in out

    def test_docx_install_hint_registered(self) -> None:
        from qaops.ingestion.registry import _INSTALL_HINTS

        assert ".docx" in _INSTALL_HINTS
        assert "qaops-ai[docx]" in _INSTALL_HINTS[".docx"]


class TestFormatEquivalenceRegression:
    """The same content as DOCX and as Markdown must normalize to byte-identical
    text, so the ingestion layer delivers identical pipeline input regardless of
    source format (Phase 24, Step 8). Downstream artifact equivalence then
    depends on provider/model determinism, not on ingestion."""

    def test_docx_and_markdown_normalize_identically(self, tmp_path: Path) -> None:
        d = _doc()
        d.add_heading("Checkout", level=1)
        d.add_paragraph("The system shall calculate totals.")
        d.add_paragraph("Apply tax", style="List Bullet")
        docx_text = load_document(_save(d, tmp_path / "spec.docx"))

        md = "# Checkout\n\nThe system shall calculate totals.\n\n- Apply tax\n"
        md_path = tmp_path / "spec.md"
        md_path.write_text(md, encoding="utf-8")
        md_text = load_document(md_path)

        assert docx_text == md_text

    def test_heading_and_table_equivalence(self, tmp_path: Path) -> None:
        d = _doc()
        d.add_heading("Limits", level=2)
        t = d.add_table(rows=2, cols=2)
        t.rows[0].cells[0].text = "Field"
        t.rows[0].cells[1].text = "Max"
        t.rows[1].cells[0].text = "Name"
        t.rows[1].cells[1].text = "50"
        docx_text = load_document(_save(d, tmp_path / "lim.docx"))

        md = "## Limits\n\n| Field | Max |\n| --- | --- |\n| Name | 50 |\n"
        md_path = tmp_path / "lim.md"
        md_path.write_text(md, encoding="utf-8")
        md_text = load_document(md_path)

        assert docx_text == md_text
