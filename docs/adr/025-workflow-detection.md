# ADR-025: Workflow detection is deterministic, and biased toward the document route

**Status:** Accepted · **Date:** 2026-07-24 · **Relates to:** ADR-022, ADR-024

## Context

Multi-entry support (ADR-022) required the user to know which entry point their
file belonged to and pass `--from`. That put pipeline knowledge on the user for
a decision the system can usually make itself.

## Decision

Add an orchestration layer above the pipeline that classifies the input,
validates preconditions, and delegates to the existing `PipelineBuilder`. It
constructs no stages itself and changes none.

1. **Classification is deterministic, with no LLM call.** Extensions resolve
   most cases outright (`.pdf`/`.docx` are prose, `.xlsx` is a spreadsheet of
   scenarios). The ambiguous formats are settled by inspecting structure: CSV
   and JSON by their headers and keys, markdown and text by whether they
   contain a scenario table or an explicitly marked scenario list.

2. **Ambiguity resolves toward the document route.** Guessing "scenarios"
   wrongly *fails the run*, because the structured parsers reject what they do
   not recognise. Guessing "document" wrongly costs extra LLM calls but still
   produces output. The asymmetry decides the default.

3. **A bare list is not a scenario list.** The first implementation treated any
   bulleted or numbered list as scenarios, which routed `examples/login.md` — a
   prose PRD with numbered acceptance criteria — down the scenario path, where
   it failed. Requirement documents routinely use numbered criteria and
   bulleted notes. List items now count only when explicitly marked with a
   `REQ-001` style reference or a known category tag, and a majority of items
   must be marked, so one stray reference in a document's notes does not
   trigger a false positive. Markdown *tables* remain a reliable signal.

4. **Pre-flight checks run before any pipeline work**: file exists and is a
   file, the optional dependency for its format is installed, and the
   provider's API key is present. These are the failures that would otherwise
   surface several stages in, after spending LLM calls. Output-collision safety
   already runs at write time (ADR-023) and is not duplicated here.

5. **`--from` still works** and skips detection entirely, so an explicit choice
   always wins.

## Consequences

- `qaops design PRD.pdf`, `qaops design Requirements.csv`, and
  `qaops design Scenarios.xlsx` all select the right workflow with no flag,
  and the CLI reports what it detected and why.
- Pre-flight runs before `create_client`, so a missing API key is caught even
  when the client would have been mocked. This changed several existing CLI
  tests, which now set a key — the production behavior is the point: fail
  before doing work, not during.
- Cost: classification is heuristic for the ambiguous formats and will
  occasionally choose the document route for something a user considers a
  scenario list. That failure is recoverable with `--from`; the reverse
  (silently mis-parsing a PRD as scenarios) is not, which is why the bias runs
  that way.
- This layer makes decisions from file structure. It is not an agent: it does
  not plan, choose tools dynamically, loop on its own output, or adapt when a
  stage fails. Calling it orchestration is accurate; calling it agentic would
  not be.
