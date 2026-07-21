# ADR-018: A DocumentLoader ingestion layer, not per-format branching

**Status:** Accepted · **Date:** 2026-07-20 · **Relates to:** ADR-001, ADR-005, ADR-016, ADR-017

## Context

A real user dropped a PDF on the CLI and hit
`input_path.read_text(encoding="utf-8")` — an immediate `UnicodeDecodeError`.
The narrow fix is a PDF branch in the CLI; the honest diagnosis is that the
CLI conflated two responsibilities, *locating* a document and *decoding* it to
normalized text, with UTF-8 read hardcoded as the only decoder. Adding a PDF
`if` would invite a DOCX `if`, an HTML `if`, and so on.

## Decision

Introduce a document-ingestion layer, the third pluggable-format abstraction in
QAOps, deliberately the same shape as providers (ADR-013) and exporters
(ADR-016):

1. **A `DocumentLoader` protocol** in `qaops/ingestion/`, structural like
   `Exporter`: `format_name`, `supported_extensions`, `load(path) -> str`.
2. **Concrete loaders**, one per format family. Implemented now:
   `TextLoader` (.txt/.md/.markdown) and `PdfLoader` (.pdf). Registered stubs:
   `DocxLoader` (.docx) and `HtmlLoader` (.html/.htm), which raise a clear
   "planned, not yet implemented" `DocumentLoadError`. The framework is
   complete; the loader set grows behind it.
3. **A registry**, `{extension: loader}` — no `if`/`else`, exactly the exporter
   pattern (and the "dict until a registry is justified" judgment of ADR-005).
4. **One dispatcher**, `load_document(path) -> str`, the single entry point the
   CLI calls instead of `read_text`. The CLI change is one line.
5. **The pipeline never learns the source format.** Its contract remains
   `RequirementInput(text=...)`; ingestion happens entirely before that
   boundary. Everything after `RequirementInput` — six stages, four exporters —
   is untouched and format-blind.

### Normalization contract

Every loader returns text through `normalize_text`, so downstream stages get
uniform input regardless of source: valid UTF-8, BOM stripped, CRLF/CR → LF,
trailing whitespace trimmed per line, 3+ blank lines collapsed to one, leading
/trailing blank lines removed. The blank-line collapse is deliberately
conservative (single blanks, which carry paragraph structure, are preserved)
and is the rule a future setting might make configurable.

### Two distinct error kinds

- `UnsupportedDocumentFormatError` — the extension has no registered loader.
  Carries the offending extension, the supported list, and an install hint, so
  the CLI renders an actionable multi-line message.
- `DocumentLoadError` — a *registered* format failed: unreadable file,
  extraction failure, a missing optional dependency, an empty/scanned PDF, or a
  stub format. A known-but-unimplemented format is not the same as an unknown
  one, and the messages say so differently.

### Optional dependencies

`pypdf` ships as the `[pdf]` extra (in `[dev]` so CI covers it), mirroring
`[gemini]` and `[excel]`. Text/Markdown need no new dependency; HTML, when
implemented, will use stdlib `html.parser`; DOCX will add a `[docx]` extra.

## Consequences

- The PDF defect is fixed by the abstraction, not a patch, and DOCX/HTML become
  drop-in additions behind the existing interface — no pipeline, model,
  exporter, or CLI change when they land.
- Loaders do extraction, not layout reconstruction: `PdfLoader` returns linear
  text and raises clearly on image-only PDFs (no silent empty run); column/table
  fidelity and OCR are future work behind the same interface, not v1 promises.
- The analyzer's `max_input_chars` guard now applies to normalized text, so an
  oversized PDF is rejected there like any other oversized input — no
  per-format special-casing.
- Cost: one more optional extra to track, and extraction quality becomes a real
  concern for binary formats. Accepted and stated honestly rather than hidden.
