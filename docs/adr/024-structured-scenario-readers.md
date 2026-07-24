# ADR-024: Structured readers for human-authored scenarios; prose stays with the analyzer

**Status:** Accepted · **Date:** 2026-07-23 · **Relates to:** ADR-018, ADR-022

## Context

Multi-entry input (ADR-022) accepted scenarios only as QAOps-shaped JSON or
CSV, which assumed the user had already run QAOps. Real QA teams keep
scenarios in spreadsheets and markdown, and asking them to convert those by
hand defeats the point of a scenario entry point.

The phase brief also proposed parsers producing `RequirementAnalysisResult`
from prose PDFs and DOCX, while stating that **no generation logic belongs in
parsers**. Those two requirements conflict: turning unstructured prose into
discrete requirements with titles, actors, and validations *is* extraction,
which needs a model. That path already exists as the `document` entry point,
where `RequirementAnalyzer` does exactly this. Building a "requirements parser"
for prose would either duplicate that stage or put an LLM inside a parser.

## Decision

Add **structured** readers for scenario documents, and leave prose to the
analyzer.

1. **XLSX** (`.xlsx`, `.xlsm`): first worksheet, first non-empty row as the
   header. Requires a recognisable title column; other columns optional.
2. **Markdown tables**: the first pipe table containing a title column.
3. **Markdown / TXT lists**: bulleted or numbered items, one scenario each,
   with `REQ-001` style tokens read as requirement references and a
   parenthesised known category (`(positive)`) lifted out of the title.
4. **Header aliasing**: names are matched case-insensitively, ignoring spaces
   and underscores, so `Scenario Name`, `scenario_name`, and `NAME` all mean
   title. Real spreadsheets do not use canonical field names.
5. **Unstructured prose fails with guidance**, naming the `document` entry
   point rather than guessing at structure. Deterministic readers stay
   deterministic; no reader calls an LLM.

Every reader produces the same record dicts the existing CSV and JSON paths
produce, so `parse_scenarios` treats all formats identically and the pipeline
continues to see only canonical domain models. `PipelineBuilder`, the
generation stages, prompts, and exporters are untouched.

## Consequences

- A team's existing Excel scenario sheet runs straight to test cases with one
  LLM call, no manual conversion.
- Cost: recognition is structural, so a document whose structure the readers do
  not recognise is rejected rather than partially understood. That is the
  intended trade - a wrong guess about which lines are scenarios would
  silently produce a bad test suite, whereas a clear rejection points at the
  entry point that does handle prose.
- The list reader's category and requirement-ID extraction is heuristic within
  a structured item. It is conservative: an unrecognised parenthetical is left
  in the title rather than dropped.
- DOCX and HTML remain registered ingestion stubs (ADR-018). Implementing them
  extends the existing `document` route and needs no new architecture, which is
  why they are not part of this phase.
