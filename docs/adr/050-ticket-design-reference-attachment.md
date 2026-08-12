# ADR-050: Ticket design/reference attachment

**Status:** Accepted · **Date:** 2026-08-11 · **Phase 35** · **Relates to:** ADR-047 (Jira-style ticket input)

## Context

Phase 32 (ADR-047) accepts a Jira-style ticket and transcribes it to Markdown that
enters the existing DOCUMENT pipeline. Teams often have design or reference material
(a spec PDF, a design DOCX, notes) that provides useful evidence beyond the ticket
text. Phase 35 lets the user optionally attach one such file to a ticket, as
ADDITIONAL EVIDENCE - without inventing requirements from it, and without a second
pipeline.

Verified constraint (from the code): `execute_run` consumes exactly one file
(`input_files[0]`) and `load_document` is one-file->text. An attachment therefore
cannot be a second file in the run input; it must become part of the single
combined document.

## Decision

Combine ticket + optional attachment into one Markdown document at the endpoint,
then run it through the unchanged pipeline.

- **Endpoint**: `POST /api/v1/design/ticket` becomes multipart (Option A) - ticket
  fields as form params plus an optional `attachment` file. One endpoint; no
  duplicate run-creation flow (still delegates to the unchanged
  `_create_and_schedule_run`).
- **Attachment extraction**: reuses the existing `load_document` ingestion to turn
  the attachment into text. No new format logic.
- **Evidence section**: appended to the normalized ticket Markdown, verbatim, in a
  fixed shape:

  ```
  ## Design / Reference Material
  Source: <filename>

  <extracted attachment text>
  ```

  It is document EVIDENCE - the existing analyzers decide what, if anything, is a
  requirement. The endpoint/normalizer never parse it into REQ-*/BR-*.
- **Supported attachment formats**: PDF, DOCX, MD/Markdown, TXT - a deliberately
  narrower set than the full document-upload formats (csv/json/xlsx are not offered
  as "design attachments" just because ingestion supports them; expandable later).
- **Acceptance-criteria removal**: the acceptance-criteria field is removed from the
  primary ticket UI. `TicketRequest.acceptance_criteria` stays optional in the
  schema (backward compatible for any existing caller). When empty, the normalizer
  now OMITS the `## Acceptance Criteria` section entirely (previously a bare heading
  was emitted) - an intentional Phase 35 normalization change, pinned by tests.

### Error behavior (all client-facing 400, never 500)

- Unsupported attachment suffix -> 400.
- Empty attachment -> 400.
- Loader failure, including a missing optional dependency (pdf/docx extra) -> 400
  with a clear "could not be processed" message (the loader error is caught, never
  surfaced as a 500).
- Attachment extracts no usable text (empty/whitespace) -> 400.
- Empty description -> 422 (unchanged; `description` remains required).

### Provenance

The run `source_name` stays ticket-anchored (`<TICKET-ID> - <title>.md`, or title
alone). The attachment's own filename is recorded only inside the evidence section's
`Source:` line - it does not alter `source_name` or any generated ID.

## Backward compatibility

- **Ticket-only (no attachment)** is byte-identical to the Phase 32 pipeline path
  for equivalent normalized input (no evidence section appended). The only
  intentional normalization change is the empty-AC omission above.
- **Existing document upload** (`POST /api/v1/design`) is unchanged.
- **Phase 31 `review_advice_enabled`** flag (`QAOPS_REVIEW_ADVICE_ENABLED`) is
  unchanged - ReviewAdvice remains opt-in and non-mandatory; the ReviewAgent is not
  touched. Phase 33 (`TestCase.assumptions`) and Phase 34 (the
  `test_case_assumptions` finding and its 50% threshold) are unchanged.

## Scope

Ticket-shaped payload plus one optional evidence file only. No Jira integration, no
second pipeline, no new agent or pipeline stage, no LLM preprocessing of the
attachment.

## Alternatives considered

- **Second file in the run input**: rejected - breaks the runner's single-file
  contract and would need multi-file ingestion the pipeline does not have.
- **A second attachment-specific endpoint**: rejected - the frontend is ours and the
  attachment is genuinely optional, so one multipart endpoint is cleaner. (Would be
  reconsidered only to protect external JSON callers, of which there are none.)
- **Parsing the attachment into requirements at the endpoint**: rejected - that is
  the pipeline's job; the attachment is evidence, not a requirements source.
