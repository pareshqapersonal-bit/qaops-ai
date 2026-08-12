# ADR-051: Multiple ticket attachments

**Status:** Accepted · **Date:** 2026-08-12 · **Phase 35B** · **Relates to:** ADR-050 (single ticket attachment)

## Context

Phase 35A (ADR-050) let a ticket carry ONE optional design/reference attachment,
extracted via the existing `load_document` and appended as a single evidence section
to the combined Markdown document. Teams frequently have more than one relevant
artifact (a spec plus mockup notes, several reference files). Phase 35B extends this
to MULTIPLE attachments, keeping the single-document / single-pipeline model.

## Decision

Accept 0, 1, or many attachments on the ticket endpoint and fold them all into one
combined Markdown document as ordered evidence.

- **API field unchanged**: the multipart field stays named `attachment`; only its
  cardinality changes to a list (`list[UploadFile]`). No `attachments` rename - a
  Phase 35A client sending a single `attachment` field still works unchanged.
- **Supported formats unchanged**: PDF, DOCX, MD, MARKDOWN, TXT. XLSX and images are
  deferred (see below).
- **AttachmentEvidence**: a small internal frozen dataclass
  (`filename`, `text`) in `qaops/ingestion/ticket_normalizer.py`. It is an
  ingestion/API value object only - NOT a pipeline/domain model, never placed in
  `qaops/models/` and never imported by any pipeline stage. It is constructed in the
  ticket endpoint after successful extraction and consumed only by the normalizer's
  combining helper.
- **Upload-order preservation**: attachments are processed and rendered in the order
  received; never sorted or de-duplicated, so two files with the same name yield two
  distinct, honest sections.
- **Combined Markdown evidence**: `append_reference_materials(markdown, evidences)`
  emits one section per attachment:

  ```
  ## Design / Reference Material
  Source: <filename>

  <extracted attachment text>
  ```

  Each attachment's text is embedded verbatim as document EVIDENCE; the existing
  pipeline decides what, if anything, is a requirement. Sections are plain
  prose/headings only, so the combined document still classifies as DOCUMENT. An
  empty list returns the markdown unchanged, and a single attachment produces output
  byte-identical to Phase 35A (the singular helper is now a thin wrapper over the
  plural one).

### Strict failure

Attachment handling is strict: if ANY attachment fails validation or extraction, the
whole request fails with a client-facing 400 - never a silent skip, never a partial
run, never a bare 500. Failures name the offending file. The failure modes mirror
Phase 35A, per file: unsupported suffix, empty file, loader failure (including a
missing pdf/docx optional dependency), and no-extractable-text all return 400. A 400
is raised before any run is created, so no partial run is left behind. Empty
description still returns 422.

### Provenance

Run `source_name` stays ticket-anchored (`<TICKET-ID> - <title>.md`, or title alone).
Each attachment's filename is recorded only in its evidence section's `Source:` line -
never in `source_name` or any generated ID.

## Deferred (explicitly out of scope for 35B)

- **XLSX**: no spreadsheet loader is added. A deterministic per-sheet representation
  is a future phase; XLSX remains unsupported and its suffix returns 400.
- **Images (PNG/JPG), OCR, and multimodal**: deferred. Images do not fit the
  text-document pipeline without either a lossy OCR dependency or a multimodal
  overhaul of the (text-only) LLM abstraction. No OCR, no multimodal, and no LLM
  abstraction change is made in 35B. This is independent of any provider (the
  pipeline consumes a text document regardless of which model backs it).

## Backward compatibility

- **Ticket-only (no attachments)** is byte-identical to the Phase 32 pipeline path.
- **Single attachment** is byte-identical to Phase 35A (pinned by a test comparing
  the plural combiner with one file against the 35A singular helper); the field name
  is unchanged, so existing single-file callers and the existing frontend flow keep
  working.
- **Existing document upload** (`POST /api/v1/design`), `_create_and_schedule_run`,
  `execute_run`, `load_document`, `classify_input`, all pipeline stages,
  CoverageValidator, QualityReviewer, ReviewAgent, the LLM abstraction/providers,
  Phase 33, Phase 34, and the Phase 31 `review_advice_enabled` flag are all
  unchanged.

## Alternatives considered

- **Rename the field to `attachments`**: rejected - an unnecessary breaking change to
  the Phase 35A contract; `attachment` as a repeated multipart field already carries
  multiple files.
- **Lenient/partial-evidence handling** (skip bad files, run with the rest): rejected -
  silent evidence loss is dangerous for a trust-critical QA tool; strict-fail keeps
  the evidence set exactly what the user provided.
- **An AttachmentEvidence domain/pipeline model**: rejected - the pipeline must keep
  consuming one text document; evidence stays an internal request-time value object.
