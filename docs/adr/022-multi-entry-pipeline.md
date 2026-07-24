# ADR-022: Multiple entry points by composing existing stages

**Status:** Accepted · **Date:** 2026-07-23 · **Relates to:** ADR-001, ADR-014, ADR-016, ADR-020

## Context

QAOps assumed every run began with a requirement document. Real QA teams often
already hold requirements or scenarios and want test design from those,
without re-deriving them from a PRD that may not exist. Forcing a document
entry point excluded most real workflows.

## Decision

Support three entry points by **composing the existing stages differently**.
No stage is modified, subclassed, or duplicated.

    DOCUMENT       analyzer -> rules -> gaps -> scenarios -> cases -> coverage
    REQUIREMENTS               rules -> gaps -> scenarios -> cases -> coverage
    SCENARIOS                                               cases -> coverage

This works because the stage contracts already line up: `BusinessRuleExtractor`
consumes a `RequirementAnalysisResult`, and `TestCaseGenerator` consumes a
`ScenarioDesignResult`. An entry point only has to construct the domain model
its first stage expects. **A stage cannot tell which route was taken** — it
receives exactly the model it always has.

1. **`EntryPoint` enum** names the three routes.
2. **`PipelineBuilder`** returns the minimal stage sequence for a route,
   slicing the same stage list rather than defining new compositions.
3. **Parsers** turn JSON and CSV into canonical domain models. They are the
   mirror image of exporters — exporters write domain models to files, parsers
   read files into domain models — and contain no generation logic and make no
   LLM calls. The CSV column layout matches the csv-bundle export, so an
   exported bundle can be edited and fed back in.
4. **IDs are always reassigned by code**, never trusted from the input file,
   per ADR-001.

### Scenario entry must carry requirements

`TestCaseGenerator` validates that generated cases reference known requirement
IDs, and that a case's requirement references belong to its own scenario
(ADR-014). A bare list of scenarios would therefore fail downstream reference
validation. The scenario parser resolves this: it uses requirements supplied
in the input when present, and otherwise **synthesizes minimal placeholder
requirements** from the IDs the scenarios reference, remapping the file's IDs
to canonical ones. A scenario CSV referencing `REQ-001` therefore works
without the user having to supply a requirements file too.

This is a real constraint discovered from the stage contracts, not an
arbitrary choice — and it is why scenario entry produces a full
`ScenarioDesignResult` rather than a bare scenario list.

## Consequences

- QAOps is a composable test-design platform rather than a PRD processor.
  Scenario entry is a single LLM call (test-case generation) versus five for
  the document route, so it is dramatically cheaper and faster.
- Exporters are unchanged and work identically from any entry point, because
  every route converges on the same `TestDesignResult`.
- Cost: placeholder requirements synthesized for scenario entry carry no real
  description, so coverage reporting for those requirements is structural
  rather than meaningful. Supplying requirements alongside scenarios yields
  richer output; the placeholder path exists so the minimal input still works.
- Future entry points (business rules, existing test cases) follow the same
  pattern: add an enum member, a parser, and a builder branch. No stage
  changes.
