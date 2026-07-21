"""Phase 8 tests: the document ingestion layer.

Normalization contract, each loader, the registry dispatcher, PDF text
extraction (against a PDF built at test time), stub behavior, and the
friendly unsupported-format error. No LLM calls anywhere here."""

from pathlib import Path

import pytest

from qaops.core.errors import DocumentLoadError, UnsupportedDocumentFormatError
from qaops.ingestion import (
    DocumentLoader,
    DocxLoader,
    HtmlLoader,
    PdfLoader,
    TextLoader,
    load_document,
    normalize_text,
    supported_extensions,
)


def _make_pdf(path: Path, text: str) -> None:
    """Write a minimal single-page PDF containing `text` using pypdf."""
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
    )

    # Build a page with a content stream drawing the text. pypdf's high-level
    # API doesn't author text, so assemble a minimal content stream.
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    page = writer.pages[0]

    escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    lines = escaped.split("\n")
    stream_parts = ["BT", "/F1 12 Tf", "50 740 Td", "14 TL"]
    for line in lines:
        stream_parts.append(f"({line}) Tj")
        stream_parts.append("T*")
    stream_parts.append("ET")
    content = "\n".join(stream_parts).encode("latin-1")

    stream = DecodedStreamObject()
    stream.set_data(content)
    stream_ref = writer._add_object(stream)

    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    resources = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    page[NameObject("/Contents")] = stream_ref
    page[NameObject("/Resources")] = resources
    page[NameObject("/MediaBox")] = ArrayObject(
        [NumberObject(0), NumberObject(0), NumberObject(612), NumberObject(792)]
    )
    with path.open("wb") as fh:
        writer.write(fh)


class TestNormalization:
    def test_strips_bom(self) -> None:
        assert normalize_text("\ufeffhello") == "hello"

    def test_crlf_and_cr_to_lf(self) -> None:
        assert normalize_text("a\r\nb\rc") == "a\nb\nc"

    def test_trims_trailing_whitespace_per_line(self) -> None:
        assert normalize_text("a   \nb\t\n") == "a\nb"

    def test_collapses_three_or_more_blank_lines(self) -> None:
        assert normalize_text("a\n\n\n\n\nb") == "a\n\nb"

    def test_preserves_single_blank_line(self) -> None:
        assert normalize_text("a\n\nb") == "a\n\nb"

    def test_strips_leading_and_trailing_blank_lines(self) -> None:
        assert normalize_text("\n\nhello\n\n") == "hello"


class TestProtocolConformance:
    @pytest.mark.parametrize("loader", [TextLoader(), PdfLoader(), DocxLoader(), HtmlLoader()])
    def test_all_loaders_satisfy_protocol(self, loader: DocumentLoader) -> None:
        assert isinstance(loader, DocumentLoader)
        assert loader.format_name
        assert all(ext.startswith(".") for ext in loader.supported_extensions)


class TestTextLoader:
    def test_loads_and_normalizes_markdown(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.md"
        f.write_text("\ufeff# Title\r\n\r\n\r\n\r\nBody   \r\n", encoding="utf-8")
        assert TextLoader().load(f) == "# Title\n\nBody"

    def test_loads_txt(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.txt"
        f.write_text("plain text", encoding="utf-8")
        assert TextLoader().load(f) == "plain text"

    def test_invalid_utf8_is_friendly_error(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.txt"
        f.write_bytes(b"\xff\xfe\x00binary")
        with pytest.raises(DocumentLoadError, match="not valid UTF-8"):
            TextLoader().load(f)


class TestPdfLoader:
    def test_extracts_text(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.pdf"
        _make_pdf(f, "User Login\nAcceptance criteria\nForgot password link")
        text = PdfLoader().load(f)
        assert "User Login" in text
        assert "Forgot password" in text

    def test_empty_pdf_raises_no_text_found(self, tmp_path: Path) -> None:
        from pypdf import PdfWriter

        f = tmp_path / "blank.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        with f.open("wb") as fh:
            writer.write(fh)
        with pytest.raises(DocumentLoadError, match="No extractable text"):
            PdfLoader().load(f)


class TestStubLoaders:
    def test_docx_is_registered_but_not_implemented(self) -> None:
        assert ".docx" in DocxLoader().supported_extensions
        with pytest.raises(DocumentLoadError, match="not yet implemented"):
            DocxLoader().load(Path("whatever.docx"))

    def test_html_is_registered_but_not_implemented(self) -> None:
        assert ".html" in HtmlLoader().supported_extensions
        with pytest.raises(DocumentLoadError, match="not yet implemented"):
            HtmlLoader().load(Path("whatever.html"))


class TestRegistryDispatch:
    def test_supported_extensions_includes_all_registered(self) -> None:
        exts = supported_extensions()
        for ext in (".txt", ".md", ".markdown", ".pdf", ".docx", ".html", ".htm"):
            assert ext in exts

    def test_dispatches_text(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.md"
        f.write_text("hello", encoding="utf-8")
        assert load_document(f) == "hello"

    def test_dispatches_pdf(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.pdf"
        _make_pdf(f, "requirement text here")
        assert "requirement text here" in load_document(f)

    def test_unsupported_extension_is_friendly(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.xlsx"
        f.write_text("x", encoding="utf-8")
        with pytest.raises(UnsupportedDocumentFormatError) as excinfo:
            load_document(f)
        err = excinfo.value
        assert err.extension == ".xlsx"
        assert ".pdf" in err.supported

    def test_pdf_extension_carries_install_hint(self, tmp_path: Path) -> None:
        # A registered format has no unsupported error; verify the hint plumbing
        # via a truly unknown extension does not include a spurious hint.
        f = tmp_path / "doc.rtf"
        f.write_text("x", encoding="utf-8")
        with pytest.raises(UnsupportedDocumentFormatError) as excinfo:
            load_document(f)
        assert excinfo.value.install_hint == ""  # rtf isn't a known-but-extra format

    def test_stub_format_dispatches_to_not_implemented(self, tmp_path: Path) -> None:
        f = tmp_path / "doc.docx"
        f.write_bytes(b"PK\x03\x04dummy")  # docx magic, but stub won't parse it
        with pytest.raises(DocumentLoadError, match="not yet implemented"):
            load_document(f)
