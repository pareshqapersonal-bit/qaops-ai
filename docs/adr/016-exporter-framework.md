# ADR-016: JSON is canonical; other exporters derive from it; CSV is intentionally lossy

**Status:** Accepted · **Date:** 2026-07-19 · **Relates to:** ADR-001 (Exporter protocol), ADR-015

## Context

Phase 6 implements the `Exporter` protocol defined in Phase 0. Four formats
(JSON, Markdown, CSV, Excel) must render the same `TestDesignResult`
deterministically, without mutating it, and without drifting apart as the
model evolves. A `TestDesignResult` is a graph (requirements ↔ scenarios ↔
test cases ↔ coverage); some target formats are flat.

## Decision

1. **JSON is the canonical serialization; the others derive from it.**
   `to_canonical_dict` runs the result through Pydantic's `model_dump(mode=
   "json")` once. Every exporter renders from that single dict, so field
   names, ordering, and enum values are defined in exactly one place. A field
   added to a model appears in JSON automatically and cannot silently
   disappear from the other formats' source data.

2. **Determinism is structural and enforced by tests.** No timestamps, no
   generated IDs, no `datetime`, no set iteration in output. Keys keep
   declaration order (`sort_keys=False`) rather than being alphabetized —
   declaration order is already stable and more readable. Tests export twice
   and assert byte-identical output (JSON/CSV/Markdown) or identical
   read-back cell values (Excel).

3. **Exporters never mutate.** They read the result and write a file; there is
   no code path that modifies the input. A test asserts the result's JSON is
   unchanged after each export.

4. **CSV is intentionally lossy; Excel carries the whole graph.** CSV emits the
   test-case table only (one row per case, list/mapping fields joined with a
   stable separator) — the artifact a QA lead imports into a tracker.
   Flattening the full graph into one CSV would be unreadable. The complete
   graph is available in JSON (canonical) and Excel (one sheet per entity:
   Coverage Summary, Requirements, Scenarios, Test Cases, Traceability).

5. **Excel writes values, not formulas.** The data is already computed
   (Phase 5), so there is nothing to recalculate; a values-only workbook is
   deterministic and needs no LibreOffice pass. A professional font is applied
   per spreadsheet conventions. `openpyxl` ships as the optional `[excel]`
   extra (in `[dev]` so CI covers it), mirroring the Gemini `[gemini]`
   pattern; a missing install raises `ExportError` with an actionable message.

## Consequences

- One serialization semantics, four renderings — the formats cannot diverge.
- Exporters make zero LLM calls (they are not pipeline stages and hold no
  client), extending the ADR-015 determinism boundary to the output layer.
- Cost: CSV consumers must accept a test-case-only view. Accepted and
  documented — the graph is one JSON or Excel export away.
