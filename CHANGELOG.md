# Changelog

All notable changes to QAOps AI are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Pre-1.0, minor versions may contain breaking changes; each is called out explicitly.

## [Unreleased]

Planned, in order (one phase merges only when the previous is complete — see CONTRIBUTING.md):

- **Phase 5 — Validation:** deterministic Coverage Validator, Traceability
  Matrix, heuristic Deduplicator (flags, never deletes — ADR-007).
- **Phase 6 — Exporters:** Markdown, CSV, XLSX, JSON.
- **Phase 7 — v1.0.0:** CLI, examples, documentation, release.

Deferred beyond v1.0 (see README non-goals): automation code generation,
test execution, docx/PDF ingestion, persistence, web UI, semantic
deduplication.

## [0.5.0-alpha] - 2026-07-19

Phase 4: manual test case generation — the final generation stage of the
Test Design pipeline. Backward compatible: additions only; no existing
stage, domain model, prompt, or provider was modified beyond pipeline
composition.

### Added

- **`TestCaseGenerator`** (`ScenarioDesignResult → TestDesignResult`):
  turns each scenario into one or more production-quality manual test
  cases with preconditions, test data, ordered steps, expected results,
  priority, type, tags, and full scenario/requirement traceability.
  Validation (all deterministic): unknown scenario refs, unknown
  requirement refs, and requirement refs not linked to the case's own
  scenario are loud `StageError`s (ADR-014); exact duplicates within a
  scenario fail loudly (ADR-012); mandatory fields and 1..N step ordering
  are enforced by the strict domain models. Coverage is left untouched
  for Phase 5.
- **Wire schemas:** `ExtractedTestStep` (no number — order from list
  position), `ExtractedTestCase` (flat, carries its own `scenario_id`),
  `TestCaseExtraction`. Step numbers and TC-* IDs are assigned by code,
  never the model (ADR-001, ADR-014).
- **Prompt template:** `test_case_generator_v1.md` — grounds cases in
  scenarios/requirements/rules, forbids invented IDs and step numbers,
  requires tester-executable steps with concrete data, bans duplicates.
- **`build_test_design_pipeline()`:** the full 5-stage composition
  (analyzer → rules → gaps → scenarios → test cases).
- **Tests:** 18 new offline tests — TC-* ID assignment, field mapping,
  code-assigned step ordering, artifact pass-through, prompt content,
  unknown scenario/requirement rejection, per-scenario cross-link
  rejection, duplicate handling (including same-title-across-scenarios
  non-duplicate), mandatory-field and invalid-priority repair retries,
  zero-case failure, stage precondition, and the full 5-stage pipeline
  across all four golden examples.
- **ADR-014:** flat wire schema, code-assigned step numbers, per-scenario
  reference scoping.

### Also included: Google Gemini provider

Built between Phases 3 and 4 to validate the ADR-002 LLM boundary. It was
never released or tagged independently — end-to-end validation could not
be completed because Gemini authentication failed at the provider level
(outside QAOps AI), so it ships for the first time as part of this
release rather than as a standalone version. No business logic, pipeline,
domain model, wire schema, or prompt changed; Anthropic behavior is
unchanged and all existing tests pass as-is.

- **`GeminiClient`** (`qaops/llm/gemini_client.py`): thin
  generate_content wrapper mirroring the AnthropicClient pattern —
  assistant→model role mapping, system instruction, temperature and
  token limits translated; every SDK failure wrapped in
  `LLMProviderError`; API key from `GEMINI_API_KEY`/`GOOGLE_API_KEY`
  env only with fail-fast `ConfigurationError` when absent; injectable
  SDK client for offline tests.
- **`create_client(settings)`** factory: configuration-driven provider
  selection via `QAOPS_PROVIDER=anthropic|gemini`; `mock` rejected as
  test-only. The evaluation script uses it, so provider switching is a
  pure environment change.
- **Settings:** `gemini_model` (env `QAOPS_GEMINI_MODEL`, default
  `gemini-2.5-flash`); provider whitelist gains `gemini`.
- **Optional extra:** `google-genai` ships as `qaops-ai[gemini]`;
  included in `[dev]` so CI lint/type/test coverage is unconditional.
- **Tests:** 12 offline provider tests — request translation (roles,
  system, temperature, token limit), response translation (text, usage,
  finish reason), SDK error wrapping, missing-key fail-fast, key-from-env
  construction, and factory selection including the env-only Gemini
  switch and mock rejection. No network calls in CI.
- **ADR-013:** second provider via factory selection and optional extra.

### Changed

- README gains a Configuration & providers section.
- Package version 0.4.0 → 0.5.0.

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

[Unreleased]: https://github.com/pareshtester/qaops-ai/compare/v0.5.0-alpha...HEAD
[0.5.0-alpha]: https://github.com/pareshtester/qaops-ai/compare/v0.4.0-alpha...v0.5.0-alpha
[0.4.0-alpha]: https://github.com/pareshtester/qaops-ai/compare/v0.3.0-alpha...v0.4.0-alpha
[0.3.0-alpha]: https://github.com/pareshtester/qaops-ai/compare/v0.2.0-alpha...v0.3.0-alpha
[0.2.0-alpha]: https://github.com/pareshtester/qaops-ai/compare/v0.1.0-alpha...v0.2.0-alpha
[0.1.0-alpha]: https://github.com/pareshtester/qaops-ai/releases/tag/v0.1.0-alpha
