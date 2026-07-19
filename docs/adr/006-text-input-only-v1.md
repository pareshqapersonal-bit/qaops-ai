# ADR-006: Plain text / Markdown input only in V1

**Status:** Accepted · **Date:** 2026-07-10

## Context

The spec asks the platform to accept BRDs and PRDs, which in the real world
arrive as .docx and PDF. Parsing those formats well (tables, headings,
embedded images, scanned pages) is a distinct engineering problem that would
dominate the first milestone and prove nothing about the core pipeline.

## Decision

- V1 input is `RequirementInput(text=..., source_name=...)`: pasted text or
  a `.txt`/`.md` file read by the CLI.
- Input size is capped by `QAOPS_MAX_INPUT_CHARS` (default 60,000). Oversized
  input raises `InputTooLargeError` with a clear message — fail fast, never
  silently truncate. Chunking of oversized documents is a future milestone.
- docx/PDF ingestion becomes a new *input stage* in V1.1 that produces the
  same `RequirementInput` model, so nothing downstream changes.

## Consequences

- The first milestone proves the pipeline, not a parser.
- Users with Word BRDs must copy text out in V1. Accepted for a first release.
