# Changelog

All notable changes to QAOps AI are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Pre-1.0, minor versions may contain breaking changes; each is called out explicitly.

## [Unreleased]

### Added

- **`csv-bundle` export format.** A new format (alongside the existing single
  file `csv`, plus `markdown`/`json`/`xlsx`) that writes six separate CSV files
  to the output directory - `Requirements.csv`, `BusinessRules.csv`,
  `Scenarios.csv`, `TestCases.csv`, `GapAnalysis.csv`, `Coverage.csv` - one
  record per row for import into a tracker. Implemented as `CsvBundleExporter`,
  a directory writer that intentionally does not implement the single-file
  `Exporter` protocol (it writes many files, not one) and derives every row
  from the same canonical dict as the other exporters, so no business logic is
  duplicated. The existing single-file `csv` exporter is unchanged. Selected
  with `-f csv-bundle`; composes with other formats in one run.
- **Temporary evaluation mode** (`evaluation_mode`, `max_requirements`; both
  off/unused by default). A real PRD generates more structured JSON than a
  single model response can return; runs truncated at `stop_reason=length`.
  When enabled, the analyzer prompt instructs the model to extract at most N
  requirements — reducing generation at the source, since capping after
  parsing cannot help — and the cap is re-enforced deterministically in code.
  Fewer requirements shrink every downstream stage, so the full pipeline
  (rules → gaps → scenarios → test cases → coverage → exports) completes on a
  real document. When disabled, the rendered prompt and all behavior are
  byte-identical to before. **This is a demonstration aid, not a scaling
  solution, and is expected to be removed when document chunking lands**
  (ADR-019).
- **Truncation diagnostics:** responses cut off by the output token limit are
  now detected via the provider's `stop_reason` and reported as such
  (`structured_output.truncated`), with the final error naming the cause and
  pointing at `max_output_tokens` — instead of a generic `JSONDecodeError`.
- **Empty-response diagnostics:** a provider returning no content is logged
  and reported distinctly from malformed JSON, with guidance about capacity
  limits, rate limiting, and free-tier models.
- **Tests:** 12 new (234 total) covering evaluation mode on/off, prompt
  injection, code-side cap enforcement when the model ignores the
  instruction, ID sequencing after capping, qaops.yaml acceptance of the new
  keys, and the truncation/empty-response error messages.
- **ADR-019:** temporary evaluation mode, pending document chunking.

### Fixed

- **Windows crash on non-Latin-1 model output.** LLM failure dumps were written
  without an explicit encoding, so the platform default (cp1252 on Windows)
  raised `UnicodeEncodeError` on characters such as `≥` — crashing the run
  inside the debug-logging path and masking the real schema failure. Now
  written as UTF-8, with the guard broadened so a diagnostic aid can never
  again mask the underlying error.
- **`openrouter_model` rejected by the CLI config loader.** The loader keeps
  its own allow-list of permitted `qaops.yaml` keys, which was not updated when
  the setting was added; `evaluation_mode` and `max_requirements` are included
  now too.

Remaining toward v1.0: document chunking for large inputs, broader real-world
evaluation, and documentation polish.

Deferred beyond v1.0 (see README non-goals): automation code generation,
test execution, persistence, web UI, semantic deduplication. (DOCX and HTML
ingestion are registered stubs, implementable behind the existing
DocumentLoader interface without further architecture.)

## [0.9.0-alpha] - 2026-07-20

Phase 8: document ingestion framework. Fixes the first real-world defect —
QAOps assumed every input was UTF-8 text and failed immediately on PDFs — by
introducing a document-ingestion layer rather than PDF-specific patching. The
pipeline contract is unchanged (`RequirementInput(text)`); everything before
it is now ingestion, everything after is analysis.

### Added

- **`qaops/ingestion/` package** — the third pluggable-format abstraction
  (after providers and exporters, same shape): a `DocumentLoader` protocol,
  concrete loaders, an `{extension: loader}` registry, and a single
  `load_document(path)` dispatcher (ADR-018).
- **Implemented loaders:** `TextLoader` (.txt/.md/.markdown) and `PdfLoader`
  (.pdf, via the optional `[pdf]` extra). `PdfLoader` extracts linear page
  text and raises a clear error on image-only/scanned PDFs rather than running
  the pipeline on emptiness.
- **Registered stub loaders:** `DocxLoader` (.docx) and `HtmlLoader`
  (.html/.htm) — recognized formats that raise a clear "planned, not yet
  implemented" message. Implementing either is a drop-in change behind the
  existing interface.
- **Normalization contract** (`normalize_text`): valid UTF-8, BOM stripped,
  CRLF/CR → LF, per-line trailing-whitespace trim, 3+ blank lines collapsed,
  leading/trailing blanks removed — so every downstream stage gets uniform
  input regardless of source format.
- **New errors:** `UnsupportedDocumentFormatError` (unknown extension, with
  supported list + install hint for a friendly multi-line CLI message) and
  `DocumentLoadError` (a registered format failed to read/extract).
- **CLI:** `qaops design` now accepts PDF as well as text/markdown; the input
  read is a single call to `load_document`. Unsupported formats produce a
  friendly, actionable message, never a traceback.
- **Tests:** 25 new offline tests (218 total) — normalization rules, each
  loader, protocol conformance, registry dispatch, real PDF extraction
  (against a PDF built at test time), empty-PDF and stub behavior, and the
  CLI running end-to-end from a PDF plus the unsupported-format UX.
- **ADR-018:** document-ingestion layer, not per-format branching.

### Changed

- New optional dependency: `pypdf>=4.0` as the `[pdf]` extra (in `[dev]` so CI
  covers it). Text/Markdown input needs no new dependency.
- The CLI's input read moved from `read_text(encoding="utf-8")` to
  `load_document`; the argument help now lists `.md, .txt, .pdf`.
- Package version 0.8.0 → 0.9.0.

## [0.8.0-alpha] - 2026-07-20

Phase 7: the command-line interface. Turns the library into a usable tool —
`qaops design <input>` runs everything and writes reports, no Python required.
CLI layer only: no pipeline stage, domain model, validator, exporter, or
provider was modified.

### Added

- **Command-line interface (`qaops design <input>`).** A QA engineer can
  process a requirement document into reports with one command — no Python.
  Runs the full six-stage pipeline and writes the configured export formats,
  printing a coverage-and-gaps summary. Options: `--format/-f` (repeatable),
  `--output-dir/-o`, `--config/-c`, `--debug`. Built on Typer; a thin
  composition root with no business logic (ADR-017).
- **`qaops.yaml` configuration**, layered under the existing settings so
  environment variables still take precedence; unknown keys and invalid
  values are rejected with friendly messages. Sample in `qaops.yaml.example`.
- **Friendly error handling:** library exceptions map to plain one-line
  messages and nonzero exit codes, never a traceback (`--debug` to opt in).
- **Tests:** 16 new offline CLI tests (193 total) — happy path, format and
  output-dir options, config loading and env-over-file precedence, and
  friendly errors for missing input, unknown format, oversized input, and
  invalid config. The pipeline's client is mocked, so the whole command runs
  in CI with no API key.
- **ADR-017:** the CLI is a thin composition root over existing components.

### Changed

- New base runtime dependencies: `typer>=0.12` and `pyyaml>=6.0`, declared in
  `[project.dependencies]` (not an extra) since the CLI is the primary
  deliverable. Verified in a clean, isolated virtual environment: installing
  only the wheel pulls them automatically and `qaops design` runs end to end
  with no manual package installation.
- mypy override extended to cover `yaml` (no stubs), scoped to that module.
- README gains a Usage section; roadmap Phase 7 complete.
- Package version 0.7.0 → 0.8.0.

## [0.7.0-alpha] - 2026-07-19

Phase 6: reporting & export framework. Implements the `Exporter`
protocol defined back in Phase 0 (ADR-001). Turns a validated
`TestDesignResult` into consumable artifacts. Zero LLM usage; exporters
never mutate their input. Backward compatible: additions only.

### Added

- **`JsonExporter`** — the canonical serialization (ADR-016). Full
  result including coverage, pretty-printed, declaration-order keys,
  round-trips back into the model. Every other exporter derives from it.
- **`MarkdownExporter`** — human-readable QA report: coverage summary
  with percentages, requirements, gap report, scenarios, and full test
  cases with step tables and traceability.
- **`CsvExporter`** — the test-case table (one row per case, list/mapping
  fields joined deterministically), RFC-4180 CRLF. Intentionally lossy;
  the full graph lives in JSON and Excel (ADR-016).
- **`ExcelExporter`** — multi-sheet workbook (Coverage Summary,
  Requirements, Scenarios, Test Cases, Traceability), values-only so no
  recalculation is needed, professional font. Needs the optional
  `[excel]` extra (`openpyxl`); a missing install raises `ExportError`
  with an actionable message.
- **`qaops/exporters/` package** with shared canonical-serialization and
  deterministic-join helpers.
- **Tests:** 24 new offline tests — protocol conformance, input
  immutability, determinism (export twice → byte-identical for
  JSON/CSV/Markdown, identical cell values for Excel), no-timestamp
  check, JSON round-trip, per-format content, and all four formats
  exported from the full six-stage pipeline across all four golden
  examples. No LLM calls in any exporter test.
- **ADR-016:** JSON canonical, derived formats, intentionally-lossy CSV.

### Changed

- New optional dependency: `openpyxl>=3.1` as the `[excel]` extra (in
  `[dev]` so CI covers it); scoped mypy override for its missing stubs.
- README gains an exporters note in Configuration & providers.
- Package version 0.6.0 → 0.7.0.

## [0.6.0-alpha] - 2026-07-19

Phase 5: coverage validation — the first fully deterministic stage.
Zero LLM calls: the "LLM generates, code validates" principle (ADR-001)
now has its validating half. Backward compatible: `CoverageReport` is
extended additively and no existing generation stage changed beyond
pipeline composition.

### Added

- **`CoverageValidator`** (`TestDesignResult → TestDesignResult` with
  `coverage` filled): pure deterministic computation from the
  traceability graph. Its constructor takes no LLM client — the
  zero-LLM guarantee is structural, not a promise (ADR-015). Computes:
  requirement coverage (covered/partial/uncovered, with partial driven
  by missing scenario categories), business-rule coverage (transitive
  via the rule's requirement), scenario coverage, a requirement→test-case
  traceability matrix, aggregate metrics with coverage percentages,
  heuristic near-duplicate flagging (identical titles or same
  scenario+requirements with ≥0.7 title overlap; flags, never deletes —
  ADR-007), and invalid-reference detection (reported, never trusted
  away). Input result is never mutated (`model_copy`).
- **`CoverageReport` extensions (additive, defaults):**
  `per_business_rule`, `per_scenario`, `metrics`, `duplicate_pairs`,
  `invalid_references`, plus `uncovered_business_rule_ids`,
  `uncovered_scenario_ids`, and `has_invalid_references` accessors. The
  legacy `suspected_duplicates` field is retained and mirrored.
- **New models:** `BusinessRuleCoverage`, `ScenarioCoverage`,
  `CoverageMetrics` (with percentage properties), `DuplicatePair`,
  `InvalidReference`.
- **`build_full_pipeline()`:** the complete 6-stage composition
  (analyzer → rules → gaps → scenarios → test cases → coverage).
- **Tests:** 21 new offline tests — determinism (identical repeated
  runs, no input mutation, no-client constructor), requirement/rule/
  scenario coverage including partial, traceability, duplicate flagging
  (identical and high-overlap) and non-flagging of distinct cases,
  invalid-reference reporting, metrics/percentages and zero-denominator
  safety, stage precondition, and the full 6-stage pipeline across all
  four golden examples. No LLM calls in any coverage test.
- **ADR-015:** deterministic validation stage with no LLM in its
  signature.

### Changed

- Package version 0.5.0 → 0.6.0.

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

[Unreleased]: https://github.com/pareshtester/qaops-ai/compare/v0.9.0-alpha...HEAD
[0.9.0-alpha]: https://github.com/pareshtester/qaops-ai/compare/v0.8.0-alpha...v0.9.0-alpha
[0.8.0-alpha]: https://github.com/pareshtester/qaops-ai/compare/v0.7.0-alpha...v0.8.0-alpha
[0.7.0-alpha]: https://github.com/pareshtester/qaops-ai/compare/v0.6.0-alpha...v0.7.0-alpha
[0.6.0-alpha]: https://github.com/pareshtester/qaops-ai/compare/v0.5.0-alpha...v0.6.0-alpha
[0.5.0-alpha]: https://github.com/pareshtester/qaops-ai/compare/v0.4.0-alpha...v0.5.0-alpha
[0.4.0-alpha]: https://github.com/pareshtester/qaops-ai/compare/v0.3.0-alpha...v0.4.0-alpha
[0.3.0-alpha]: https://github.com/pareshtester/qaops-ai/compare/v0.2.0-alpha...v0.3.0-alpha
[0.2.0-alpha]: https://github.com/pareshtester/qaops-ai/compare/v0.1.0-alpha...v0.2.0-alpha
[0.1.0-alpha]: https://github.com/pareshtester/qaops-ai/releases/tag/v0.1.0-alpha
