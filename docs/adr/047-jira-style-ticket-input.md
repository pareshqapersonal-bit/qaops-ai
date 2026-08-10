# ADR-047: Jira-style ticket input via deterministic normalization

**Status:** Accepted · **Date:** 2026-08-09 · **Phase 32A** · **Relates to:** ADR-028 (API schemas), the document ingestion path

## Context

QAOps accepts requirement documents (PDF/DOCX/MD/TXT/CSV/JSON/XLSX) that flow
through a single deterministic pipeline: RequirementAnalyzer → … →
CoverageValidator, then QualityReviewer (ADR-045) and ReviewAgent (ADR-046).

We want to accept a Jira-style ticket (title, description, acceptance criteria,
optional id/priority/labels) and produce the SAME artifacts, WITHOUT building a
second pipeline, a Jira integration, or any ticket-specific downstream stage.

Repository inspection established the key fact: the DOCUMENT entry point already
reduces any input to `RequirementInput(text, source_name)` via `load_document`,
and `execute_run` consumes whatever file sits in `run.input_dir`, source-agnostic.
So a ticket only needs to become a normalized Markdown document upstream of the
existing flow.

## Decision

Add a thin ticket input layer; reuse everything downstream.

1. **`TicketRequest`** (request-only schema, not a domain/pipeline model): title
   and description required; `acceptance_criteria` a list with empty allowed;
   optional `ticket_id`, `priority`, `labels`.
2. **`TicketNormalizer`** (`qaops/ingestion/ticket_normalizer.py`): a pure,
   deterministic transcription of a ticket into Markdown - `# title`, an optional
   `Ticket:/Priority:/Labels:` header (lines only when supplied), `## Description`
   verbatim, and `## Acceptance Criteria` as a verbatim numbered list. It never
   invents requirements, business rules, expected values, or criteria, never
   rewrites content, and emits no Markdown table or scenario marker (so
   `classify_input` keeps it on the DOCUMENT route). No LLM.
3. **Shared run-creation helper** `_create_and_schedule_run(input_name, contents,
   suffix, background)`: the source-agnostic tail extracted verbatim from
   `submit_design` (store.create → sanitize → write_bytes → load_settings →
   schedule execute_run → RunCreatedResponse). Both the document upload and the
   ticket endpoint delegate to it - no duplicated run-creation logic.
4. **`POST /api/v1/design/ticket`**: validates `TicketRequest`, normalizes to
   Markdown, encodes UTF-8, resolves provenance, and calls the shared helper. The
   `.md` flows through the existing DOCUMENT pipeline unchanged.

### Provenance

`source_name` carries `"<TICKET-ID> - <title>"` (or the title alone when no id)
via the on-disk `.md` filename, which `_prepare_input` already turns into
`RequirementInput.source_name` and which reaches `review_report.source_name` /
`review_advice.source_name`. Generated REQ-*/BR-*/SC-*/TC- ids are untouched; the
filename is sanitized by the existing `_sanitize_filename`, so `source_name` is the
sanitized form - accepted deliberately, with no change to the ingestion path.

## Scope

Phase 32A accepts a ticket-shaped payload only. It does NOT implement Jira
authentication, REST integration, retrieval, updates, issue creation, publishing
back, or synchronization; no new agent, no ticket-specific analyzer/generator, and
no second pipeline.

## Consequences

- A Jira-style ticket produces the full artifact set (requirements → … → coverage,
  ReviewReport, ReviewAdvice when enabled) through the existing pipeline.
- Missing ticket detail becomes genuine gaps via the existing GapAnalyzer /
  TestConditionAnalyzer - never fabricated by the normalizer.
- Existing document-upload behaviour is unchanged (the helper was extracted
  verbatim; the two upload-specific 400 checks stay in `submit_design`).
- A clean future-Jira seam: a real connector would map an issue to `TicketRequest`
  and call the same normalizer + helper, with no pipeline change.

## Alternatives considered

- **A second, Jira-specific pipeline:** rejected - duplicates the pipeline and
  violates the single-generator invariant.
- **Content-type-overloading the existing `/api/v1/design`:** rejected - a
  multipart-or-JSON handler is messier and yields a confusing OpenAPI schema; a
  thin second endpoint sharing the run-creation helper is cleaner and still
  non-duplicative.
- **Normalizer converting acceptance criteria into business rules:** rejected -
  that is the BusinessRuleExtractor's job; the normalizer only transcribes.
