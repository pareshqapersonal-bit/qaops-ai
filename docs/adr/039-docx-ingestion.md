# ADR-039: DOCX ingestion via the existing loader abstraction

**Status:** Accepted · **Date:** 2026-08-01 · **Relates to:** ADR-018 (document ingestion abstraction)

## Context

QAOps accepted PDF, plain text, and Markdown as requirement documents. Word
`.docx` was a registered-but-unimplemented format: the `DocxLoader` placeholder
raised a clear "planned, not yet implemented" error, and its own docstring
predicted the drop-in follow-up — "add python-docx extraction here, add the
[docx] extra, done. No other part of QAOps changes." Phase 24 is that follow-up.

The ingestion abstraction from ADR-018 already provides exactly the architecture
Phase 24 asked for:

- a `DocumentLoader` protocol (the parser interface),
- an extension registry with `load_document(path)` as the single dispatch entry
  point (the parser factory),
- concrete per-format loaders (`TextLoader`, `PdfLoader`, and now `DocxLoader`),
- a shared `normalize_text` contract every loader's output passes through.

Downstream stages receive only normalized UTF-8 text and never learn the source
format. So no new architecture was needed and none was added — the pipeline
(RequirementAnalyzer through Coverage) and the provider/execution architecture
are untouched.

## Decision

Implement `DocxLoader.load()` with python-docx and wire a `[docx]` optional
extra. The loader walks the document's block stream (paragraphs and tables) in
document order and renders a linear, Markdown-shaped text so the analyzer sees
the same structural cues it already gets from Markdown input:

- **Title / headings** → ATX headings (`#`, `##`, …), depth from the paragraph's
  heading style; the core-properties title, when present, leads as a top `#`.
- **Numbered list items** → `1. ` lines; **bullet items** → `- ` lines. List
  membership is read from the paragraph style (and numbering properties), since
  Word stores it there, not in the text.
- **Plain paragraphs** → their text.
- **Tables** → flattened to pipe-delimited rows with a header separator (Step 5
  "flatten if necessary"), preserving column/value association as readable text.

The rendered text is passed through the shared `normalize_text`, so DOCX output
satisfies the identical contract as every other loader.

### The normalized document model is normalized text

ADR-018 fixed the cross-format contract as normalized UTF-8 text, not a bespoke
structured object. Phase 24 honours that: the "identical internal model" both
parsers must produce (Step 5) is the normalized text, and the DocxLoader's job
is to render Word structure into that text faithfully. This is why the same
requirement content stored as DOCX and as Markdown normalizes to byte-identical
text — and therefore, because the pipeline is deterministic from its text input,
produces identical requirements, rules, gaps, scenarios, conditions, cases, and
coverage.

### Format support and rejection

Supported input now includes `.docx` alongside `.pdf`, `.txt`, `.md`. Binary
office and archive formats that are NOT requirement documents — `.doc`
(legacy binary Word), `.ppt`, `.xls`, `.zip`, images — are unregistered and
already produce a friendly `UnsupportedDocumentFormatError` listing the
supported formats; a `.doc` or renamed non-package file that reaches the loader
raises a clear "not a valid Word .docx" `DocumentLoadError`. Plain text and
Markdown remain first-class supported input (they are the primary text path);
they are deliberately NOT rejected, since doing so would be a backward-incompat
regression and contradict "do not redesign / maintain compatibility".

## Consequences

- Word documents are now first-class requirement input with no downstream
  changes; the UI already accepted `.docx` and is labelled "Upload a requirement
  document", so no UI change was required.
- The empty-document and malformed-file paths raise `DocumentLoadError` with
  clear causes, mirroring the PDF loader, rather than running the pipeline on
  emptiness.
- `python-docx` is an optional `[docx]` extra; a missing install raises a
  friendly message naming the install command, consistent with `[pdf]`.

## Limitations / future work

- Table flattening is linear (pipe rows); merged cells are rendered by their
  text content without span reconstruction. A table nested inside another
  table's cell is currently **omitted** — `cell.text` does not recurse into a
  nested table, so its text is dropped rather than rendered. This fails safe
  (surrounding content is unaffected and nothing is misattributed) and is
  uncommon in requirement documents; recursing into nested tables is a candidate
  future enhancement.
- Images, text boxes, headers/footers, footnotes, and tracked-changes markup are
  not extracted (body paragraphs and tables only).
- List rendering emits a normalized marker (`- ` / `1. `) rather than preserving
  original numbering or nesting depth; this matches how Markdown input is
  already treated downstream.
- HTML remains a registered stub, unchanged by this ADR.
