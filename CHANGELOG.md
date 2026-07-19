# Changelog

All notable changes to QAOps AI are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Pre-1.0, minor versions may contain breaking changes; each is called out explicitly.

## [Unreleased]

Planned, in order (one phase merges only when the previous is complete — see CONTRIBUTING.md):

- **Phase 1 — LLM abstraction:** `LLMClient` protocol, `AnthropicClient`,
  `MockLLMClient`, structured-output parse → validate → retry loop (ADR-002).
- **Phase 2 — Requirement analysis:** Requirement Analyzer, Business Rule
  Extractor, Ambiguity & Gap Report as a first-class output.
- **Phase 3 — Scenario Generator:** positive/negative, BVA, EP, input
  validation, error handling, RBAC, state transitions, integration.
- **Phase 4 — Test Case Generator:** full manual test cases with requirement
  traceability.
- **Phase 5 — Validation:** deterministic Coverage Validator, Traceability
  Matrix, heuristic Deduplicator (flags, never deletes — ADR-007).
- **Phase 6 — Exporters:** Markdown, CSV, XLSX, JSON.
- **Phase 7 — v1.0.0:** CLI, examples, documentation, release.

Deferred beyond v1.0 (see README non-goals): automation code generation,
test execution, docx/PDF ingestion, persistence, web UI, semantic
deduplication.

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

[Unreleased]: https://github.com/pareshtester/qaops-ai/compare/v0.1.0-alpha...HEAD
[0.1.0-alpha]: https://github.com/pareshtester/qaops-ai/releases/tag/v0.1.0-alpha
