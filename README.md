# QAOps AI

AI-powered Quality Engineering platform. **Version 1: Test Design Agent** — accepts a software requirement (user story, BRD/PRD text, acceptance criteria), analyzes it like a senior QA engineer, reports ambiguities and gaps, and generates professional manual test scenarios and test cases with deterministic coverage validation and traceability.

## Design principles

- **LLM generates; code validates.** Requirement analysis, scenarios, and test cases come from the model. IDs, traceability, coverage math, deduplication, and export are pure deterministic Python — the platform never asks the AI to grade its own homework.
- **Typed data between stages.** Every pipeline stage consumes one Pydantic model and produces another. Raw dicts never cross a stage boundary.
- **One LLM boundary.** All model calls go through the `LLMClient` interface. Anthropic and Gemini are the shipped implementations, selected purely by configuration; a `MockLLMClient` powers the entire unit test suite, so CI never needs an API key.
- **Extensible by protocol, not by registry.** Future agents (defect triage, regression impact, ...) implement the `Agent` protocol and reuse `llm/`, `models/`, `exporters/`, and `config/` unchanged.

## Architecture

```
qaops/
├── core/        # PipelineStage / Agent / Exporter protocols, Pipeline runner,
│                #   deterministic ID generation, error hierarchy
├── models/      # Requirement, BusinessRule, Gap, Scenario, TestCase,
│                #   CoverageReport, TraceabilityMatrix (Pydantic, strict)
├── config/      # QAOpsSettings (pydantic-settings, QAOPS_* env overrides)
├── llm/         # LLMClient, AnthropicClient, GeminiClient, MockLLMClient,
│                #   create_client factory, structured-output retry loop,
│                #   versioned PromptLoader
├── prompts/     # Versioned prompt templates (analyzer_v1, rule_extractor_v1,
│                #   gap_analyzer_v1)
├── pipelines/
│   └── test_design/  # RequirementAnalyzer, BusinessRuleExtractor, GapAnalyzer,
│                     #   ScenarioGenerator, wire schemas; TestCaseGenerator
│                     #   arrives in Phase 4
├── validation/  # CoverageValidator, Deduplicator — zero LLM calls      (Phase 5)
├── exporters/   # Markdown / CSV / XLSX / JSON                          (Phase 6)
└── cli/         # qaops design <input> --format xlsx                    (Phase 7)
```

## Pipeline

```
RequirementInput → RequirementAnalyzer → GapAnalyzer → BusinessRuleExtractor
    → ScenarioGenerator → TestCaseGenerator → CoverageValidator → Exporters
```

The Gap Report is a first-class output: before designing tests, the agent reports missing validations, undefined behaviors, and ambiguities — with the question a QA engineer would ask to close each gap.

## Golden examples

`examples/` contains four permanent regression fixtures (`login.md`,
`checkout.md`, `video_playback.md`, `fund_transfer.md`) — realistic
requirement documents with deliberate gaps. They ground unit tests today and
scenario/test-case generation plus live-eval review in later phases.

## Development

```bash
pip install -e ".[dev]"
pytest              # unit tests (mocked LLM)
pytest -m llm       # live LLM evals (requires ANTHROPIC_API_KEY)
ruff check . && ruff format --check .
mypy qaops tests
```

## Configuration & providers

Configuration is environment-driven — see `.env.example`; every setting has a
`QAOPS_*` override. Provider selection needs no code change:

| Setting | Env var | Default |
|---|---|---|
| Provider | `QAOPS_PROVIDER` | `anthropic` |
| Anthropic model | `QAOPS_MODEL` | `claude-sonnet-4-6` |
| Gemini model | `QAOPS_GEMINI_MODEL` | `gemini-2.5-flash` |

API keys come from the environment only, never config files:
`ANTHROPIC_API_KEY` for Anthropic; `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) for
Gemini. Gemini support installs via the optional extra:
`pip install "qaops-ai[gemini]"` (included in `[dev]`, so CI always covers
it). Prompts are tuned against Anthropic models; judge Gemini output quality
with `scripts/evaluate_analysis.py` before relying on it (ADR-013).

## Roadmap

| Phase | Deliverable | Status |
|---|---|---|
| 0 | Skeleton, domain models, protocols, config, CI | ✅ |
| 1 | LLM abstraction: Anthropic + mock clients, structured-output retry | ✅ |
| 2 | Requirement Analyzer, Business Rule Extractor, Gap Report | ✅ |
| 3 | Scenario Generator (BVA, EP, negative, RBAC, state transitions) | ✅ |
| 4 | Test Case Generator | — |
| 5 | Coverage Validator, Traceability Matrix, Deduplicator | — |
| 6 | Exporters (Markdown, CSV, XLSX, JSON) | — |
| 7 | CLI, examples, docs, v1.0 release | — |

**V1 non-goals:** automation code generation (Selenium/Playwright/etc.), test execution, defect analysis, docx/PDF ingestion, persistence, web UI, semantic deduplication.
