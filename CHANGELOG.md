# Changelog

All notable changes to QAOps AI are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Pre-1.0, minor versions may contain breaking changes; each is called out explicitly.

## [Unreleased]

Planned, in order (one phase merges only when the previous is complete — see CONTRIBUTING.md):

- **Phase 4 — Test Case Generator:** full manual test cases with requirement
  traceability.
- **Phase 5 — Validation:** deterministic Coverage Validator, Traceability
  Matrix, heuristic Deduplicator (flags, never deletes — ADR-007).
- **Phase 6 — Exporters:** Markdown, CSV, XLSX, JSON.
- **Phase 7 — v1.0.0:** CLI, examples, documentation, release.

Deferred beyond v1.0 (see README non-goals): automation code generation,
test execution, docx/PDF ingestion, persistence, web UI, semantic
deduplication.

## [0.4.0-alpha] - 2026-07-19

Phase 3: scenario generation. No test cases, priorities, test data, or
expected results — those are Phase 4. Backward compatible: additions
only.

### Added

- **`ScenarioGenerator`** (`RequirementAnalysisResult → ScenarioDesignResult`):
  scenario design across functional, positive, negative, boundary-value,
  equivalence-partition, input-validation, error-handling, CRUD, permission,
  state-transition, integration, and UI techniques, grounded in extracted
  requirements and business rules. Zero scenarios and unknown `REQ-*`
  references are loud `StageError`s; exact duplicates (same category +
  normalized title) fail the stage (ADR-012).
- **`ScenarioDesignResult`** domain model: composes the untouched Phase 2
  analysis with the generated scenarios.
- **Wire schemas:** `ExtractedScenario` / `ScenarioExtraction` — ID-less,
  category validated against the `ScenarioCategory` enum so an invalid
  category triggers the repair retry loop.
- **Prompt template:** `scenario_generator_v1.md` — enumerates valid
  categories with definitions, demands grounding in requirements/rules,
  bans duplicates and invented IDs, defers steps/data/results to Phase 4.
- **`build_scenario_pipeline()`:** 4-stage composition (analyzer → rules →
  gaps → scenarios).
- **Tests:** 14 new offline tests (102 total) — SC-* ID assignment, category
  mapping across all techniques, composition immutability, prompt content,
  unknown-reference rejection, invalid-category repair retry, duplicate
  rejection (including the same-title-different-category non-duplicate),
  zero-scenario failure, stage preconditions, a Phase 3 boundary check, and
  a full 4-stage run parametrized over all four golden examples.
- **ADR-012:** duplicate policy split between generation and validation.

### Changed

- `ScenarioCategory` gains `FUNCTIONAL` (backward-compatible addition).
- Package version 0.3.0 → 0.4.0.

## [0.3.0-alpha] - 2026-07-19

Phase 2: the requirement-analysis pipeline. First LLM-backed stages;
no scenario or test-case generation yet. Backward compatible: additions
only, no existing public API changed.

### Added

- **`qaops/pipelines/test_design/`:**
  - `RequirementAnalyzer` (`RequirementInput → RequirementAnalysisResult`):
    structured requirement extraction; enforces the input-size guardrail
    before any tokens are spent; zero extracted requirements is a loud
    `StageError`.
  - `BusinessRuleExtractor`: extracts rules linked to supplied `REQ-*` IDs;
    assigns `BR-*` IDs; unknown references raise `StageError`.
  - `GapAnalyzer`: Ambiguity & Gap Report as a first-class output — severity
    (blocker/major/minor), affected requirement, and the exact question to
    ask the BA/PO. Empty report is a valid outcome.
  - Wire schemas (`schemas.py`): ID-less, strict LLM output contracts,
    separate from domain models (ADR-011).
  - `build_analysis_pipeline()`: composes the three stages.
- **`RequirementAnalysisResult`** domain model (progressively enriched
  aggregate; retains source text for downstream grounding).
- **Prompt templates v1:** `analyzer_v1.md`, `rule_extractor_v1.md`,
  `gap_analyzer_v1.md` — grounding rules ("extract only what is stated"),
  verbatim source excerpts, reference-only ID usage.
- **Golden examples** (`examples/`): `login.md`, `checkout.md`,
  `video_playback.md`, `fund_transfer.md` — permanent regression fixtures
  with deliberate gaps, documented in `examples/README.md`.
- **Tests:** 21 new offline tests (88 total) — ID assignment, wire-to-domain
  mapping, prompt content checks, guardrail fail-fast (no LLM call on
  oversized input), repair-retry integration, failure persistence, unknown
  reference rejection, stage-order preconditions, composed pipeline run, and
  a Phase 2 boundary check (no scenario/test-case fields exist).
- **ADR-011:** wire schemas separate from domain models.

### Changed

- Package version 0.2.0 → 0.3.0.

## [0.2.0-alpha] - 2026-07-19

Phase 1: the LLM abstraction layer (ADR-002). Still no business logic —
this release delivers the single boundary every pipeline stage will use.
Backward compatible: no existing public API changed.

### Added

- **`qaops/llm/` package:**
  - `LLMClient` runtime-checkable protocol — the single LLM boundary.
  - `AnthropicClient`: thin Messages-API wrapper; SDK transport retries
    configurable; every SDK failure wrapped in `LLMProviderError`; API key
    resolved from `ANTHROPIC_API_KEY` only; injectable SDK client for tests.
  - `MockLLMClient`: ordered script of strings / `LLMResponse` objects /
    exceptions, records all requests, fails loudly when over-called (ADR-008).
  - `generate_structured()`: parse → Pydantic-validate → retry-with-feedback
    loop; failed responses echoed back to the model with the validation error;
    after exhausting retries raises `LLMResponseFormatError` carrying all raw
    responses, optionally persisting them to a failure directory.
  - `extract_json_payload()`: strips markdown fences and surrounding prose.
  - `PromptLoader`: versioned `<name>_<version>.md` templates with strict
    `string.Template` rendering — missing and unknown variables both fail
    (ADR-010).
  - LLM models (`LLMRequest`, `LLMMessage`, `LLMResponse`, `LLMUsage`) and
    errors (`LLMProviderError`, `LLMResponseFormatError` extending core
    `LLMError`).
- **`qaops/prompts/` package:** template home with naming convention;
  first templates ship in Phase 2.
- **Tests:** 34 new offline tests (67 total) covering the mock client, the
  retry loop (including hallucinated-field rejection via `extra="forbid"`),
  payload extraction, prompt loading/rendering, and `AnthropicClient`
  request/response translation via an injected SDK stub. One live eval
  marked `@pytest.mark.llm`, excluded from CI.
- **ADR-010:** prompt templating via `string.Template`.

### Changed

- New runtime dependency: `anthropic>=0.60`.
- Package version 0.1.0 → 0.2.0.

## [0.1.0-alpha] - 2026-07-19

Phase 0: architecture foundation. No LLM calls exist yet; this release
establishes the contracts every later phase builds on.

### Added

- **Domain models** (`qaops/models/`): `Requirement`, `BusinessRule`, `Gap`,
  `GapReport`, `Scenario`, `TestStep`, `TestCase`, `RequirementCoverage`,
  `TraceabilityMatrix`, `CoverageReport`, `TestDesignResult`. Strict Pydantic
  v2 (`extra="forbid"`), enforced ID patterns, at-least-one requirement link
  per scenario/test case, sequential step numbering (ADR-003).
- **Core** (`qaops/core/`): `PipelineStage` / `Agent` / `Exporter` protocols,
  sequential `Pipeline` runner with per-stage error wrapping (`StageError`),
  deterministic `IdGenerator` for `REQ-*`/`BR-*`/`SC-*`/`TC-*` IDs (ADR-001),
  typed exception hierarchy (`QAOpsError` and subclasses).
- **Configuration** (`qaops/config/`): `QAOpsSettings` via pydantic-settings
  with `QAOPS_*` environment overrides, validated provider/format/temperature,
  input-size guardrail; API key from `ANTHROPIC_API_KEY` env only (ADR-009).
- **Test suite:** 33 pytest tests covering model validation, ID generation,
  pipeline execution and error wrapping, and settings (ADR-008).
- **CI:** GitHub Actions on Python 3.12/3.13 — ruff lint, ruff format check,
  mypy `--strict`, pytest with `-m "not llm"`, build verification.
- **Documentation:** README with architecture and roadmap; nine Architecture
  Decision Records (`docs/adr/`).

[Unreleased]: https://github.com/pareshtester/qaops-ai/compare/v0.4.0-alpha...HEAD
[0.4.0-alpha]: https://github.com/pareshtester/qaops-ai/compare/v0.3.0-alpha...v0.4.0-alpha
[0.3.0-alpha]: https://github.com/pareshtester/qaops-ai/compare/v0.2.0-alpha...v0.3.0-alpha
[0.2.0-alpha]: https://github.com/pareshtester/qaops-ai/compare/v0.1.0-alpha...v0.2.0-alpha
[0.1.0-alpha]: https://github.com/pareshtester/qaops-ai/releases/tag/v0.1.0-alpha
