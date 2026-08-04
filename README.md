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
│                     #   ScenarioGenerator, TestCaseGenerator, wire schemas
├── validation/  # CoverageValidator, Deduplicator — zero LLM calls      (Phase 5)
├── ingestion/   # DocumentLoader: text/markdown + PDF + DOCX -> normalized text
├── exporters/   # JSON (canonical), Markdown, CSV, Excel — all derive from JSON
└── cli/         # qaops design <input> --format xlsx                    (Phase 7)
```

## Pipeline

```
RequirementInput → RequirementAnalyzer → GapAnalyzer → BusinessRuleExtractor
    → ScenarioGenerator → TestConditionAnalyzer → TestCaseGenerator
    → CoverageValidator → Exporters
```

The Gap Report is a first-class output: before designing tests, the agent reports missing validations, undefined behaviors, and ambiguities — with the question a QA engineer would ask to close each gap.

Runs are resilient to mid-pipeline failure: each completed stage is checkpointed to the run workspace, so if a later stage fails the artifacts from completed stages are still available to download, and the run can be resumed from the last checkpoint without re-running the stages that already succeeded (in-process resume; ADR-040).

An Orchestrator Agent reasons about *how* each run executes — it builds an execution plan (which stages run, which are reused from checkpoints, and why), and runs a goal-driven loop that observes the outcome of each act, decides whether to continue, resume, or stop, and reflects with recommendations. It manages execution until the goal is reached or a bounded stop condition (max resume attempts, clarification needed, or manual review needed). The agent only orchestrates: every artifact is still produced by the deterministic pipeline, per-stage retry stays owned by the executor, and a run with no agent intervention behaves identically to a plain pipeline run (ADR-041, ADR-042).

### Evidence-bound test conditions

Between scenarios and test cases the agent derives **test conditions** — single
testable propositions, each carrying its evidence (the requirement or rule it
comes from and the test-design technique it applies: boundary, equivalence,
negative, eligibility, state transition, rule combination, and so on). Test
cases are then generated per condition, so one scenario can legitimately yield
several cases (for example a stated "quantity ≥ 2" rule produces boundary cases
at 1, 2, and 3) without inventing behavior the documents do not define. A
condition whose expected behavior is not documented is kept, flagged as a gap,
and its case is marked provisional rather than guessed. When a
requirement-analysis gap blocks the behavior a condition would check (for
example an undefined exact-copy string), that condition is marked unresolved and
linked to the gap, so a known ambiguity cannot hide behind 100% condition
coverage. There is no fixed
scenario-to-case ratio; counts follow the evidence, within configurable
expansion bounds. Each condition's QA technique then drives how many cases it
produces: a boundary condition with a documented threshold yields below/at/above
cases, an equivalence condition yields one case per documented partition, and a
single-dimension condition yields one — all decided deterministically from the
documented values, never invented. When a bound truncates generation the result says so, and
coverage is reported across requirement, business-rule, scenario, and condition
dimensions — measuring how much of what was identified has a test, not claiming
that testing is exhaustive.

## Usage

Process a requirement document into test-design reports with one command:

```bash
pip install qaops-ai            # brings the qaops CLI
export ANTHROPIC_API_KEY=...    # or configure Gemini (see below)
qaops design examples/login.md
```

That runs the full pipeline (analyze → rules → gaps → scenarios → test cases →
coverage) and writes the configured report formats to the output directory,
printing a coverage-and-gaps summary as it goes. The input may be Markdown,
plain text, PDF, or Word (.docx); PDF support installs via `pip install "qaops-ai[pdf]"`
and DOCX via `pip install "qaops-ai[docx]"`.
Options:

```bash
qaops design spec.md -f json -f markdown -f csv -f xlsx   # choose formats
qaops design spec.md -f csv-bundle                       # 6 CSVs: Requirements, BusinessRules, Scenarios, TestCases, GapAnalysis, Coverage
qaops design Requirements.csv                            # workflow detected automatically
qaops design Scenarios.xlsx                              # detected as scenarios: test cases only
qaops design scenarios.csv --from scenarios              # --from still overrides detection
qaops design spec.md -o reports/                          # output directory
qaops design spec.md -c path/to/qaops.yaml               # explicit config
qaops design spec.md --debug                             # full tracebacks
```

Configuration is optional. Drop a `qaops.yaml` in the working directory to set
defaults; environment variables (`QAOPS_*`) still override it:

```yaml
provider: anthropic
default_export_formats: [markdown, json]
output_dir: output
temperature: 0.2
```

Errors are reported as plain messages with a nonzero exit code, never a Python
traceback (use `--debug` to see one). Excel export needs the optional extra:
`pip install "qaops-ai[excel]"`.

## HTTP API (local)

QAOps also runs as a local HTTP service, exposing the same pipeline over a REST
API so a web UI can be built on top of it. It is a second interface to the same
orchestration, not a separate implementation.

Install the API extra and start the server:

```bash
pip install "qaops-ai[api]"
uvicorn qaops.api.app:app --reload
```

Interactive docs are at `http://127.0.0.1:8000/docs`.

### Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness and version. No LLM call. |
| GET | `/api/v1/models` | Providers and discovered models (Phase 15). |
| POST | `/api/v1/design` | Upload a file; returns `202` with a run id. |
| GET | `/api/v1/runs/{id}` | Run status and, once done, a summary. |
| GET | `/api/v1/runs/{id}/artifacts` | Report metadata for a run. |
| GET | `/api/v1/runs/{id}/artifacts/{name}` | Download one report. |

### Asynchronous run lifecycle

A design can take minutes, so submission does not block. `POST /api/v1/design`
validates the upload, creates a run, schedules background execution, and
returns immediately:

```bash
curl -F "file=@examples/login.md" http://127.0.0.1:8000/api/v1/design
# {"run_id": "run_abc123...", "status": "queued"}
```

Poll the run until it completes:

```bash
curl http://127.0.0.1:8000/api/v1/runs/run_abc123...
# {"run_id": "...", "status": "completed", "entry_point": "document",
#  "summary": {"requirements": 6, "scenarios": 12, "test_cases": 14, ...}}
```

A run moves through `queued → running → completed | failed`. The workflow
(document / requirements / scenarios) is detected from the file — no `--from`
needed. A failure after submission sets `status: failed` with a safe error
message rather than turning the original request into an error.

List and download reports:

```bash
curl http://127.0.0.1:8000/api/v1/runs/run_abc123.../artifacts
curl -OJ http://127.0.0.1:8000/api/v1/runs/run_abc123.../artifacts/login.json
```

### Local runtime storage

Each run gets an isolated workspace under `~/.qaops/runs/<run_id>/` (override
with `QAOPS_RUNTIME_DIR`), split into `input/` and `output/`. Uploads are never
written into the repository or `examples/`.

**Run state is in memory.** A process restart loses all run status — the
on-disk workspaces remain, but the registry that indexes them does not. This is
intentional for local, single-process use; a persistent store is a later phase.

CORS origins default to common localhost frontend ports and are configurable
via `QAOPS_CORS_ORIGINS` (comma-separated). The CLI is unaffected by any of
this and continues to work without starting the API.

## Web UI (local)

A React + TypeScript single-page app in `frontend/` provides a browser
interface over the HTTP API: upload a requirement document, watch the run
progress, review the generated QA results, and download artifacts — no CLI or
Swagger needed. The frontend is a thin presentation layer; all pipeline
behavior stays in the backend (ADR-032).

### Prerequisites

- Node.js 20+ and npm 10+ (built and tested on Node 22 / npm 10).
- A running QAOps backend (see the HTTP API section above).

### Run it locally

Start the backend in one terminal:

```bash
python -m uvicorn qaops.api.app:app --reload
```

Start the frontend in another:

```bash
cd frontend
npm install
npm run dev
```

Then open the URL Vite prints (default `http://localhost:5173`). The backend
runs on `http://127.0.0.1:8000` by default.

### Configuration

The backend base URL is configurable via `VITE_API_BASE_URL` (default
`http://127.0.0.1:8000`). To point the UI at a different backend, create
`frontend/.env.local`:

```
VITE_API_BASE_URL=http://127.0.0.1:8000
```

### CORS

The backend already allows the Vite dev origins (`http://localhost:5173` and
`http://127.0.0.1:5173`) out of the box, so no extra configuration is needed for
local development. Additional origins can be set via `QAOPS_CORS_ORIGINS`
(comma-separated) on the backend.

### Frontend developer commands

From `frontend/`: `npm run dev` (dev server), `npm run build` (production
build), `npm run test` (Vitest), `npm run typecheck` (tsc), `npm run lint`
(ESLint).

## Deployment (Render, single service)

QAOps deploys as **one Render Web Service** with one public URL: FastAPI serves
both the API and the built React frontend from the same origin (ADR-033).

### Architecture

```
Browser --HTTPS--> Render Web Service
                     |-- FastAPI API (/health, /api/v1/...)
                     |-- React/Vite production build (served by FastAPI)
                     `-- QAOps pipeline --> LLM providers
```

Local development keeps the split origin (Vite `:5173` + FastAPI `:8000`, see
the Web UI section). Production is same-origin: the frontend calls `/api/v1/...`
relative to the serving host, so no backend URL is baked into the bundle.

### Build and start commands

- **Build:** `pip install -e ".[api,openrouter,gemini,excel,pdf]"` then, in
  `frontend/`, `npm ci && npm run build`.
- **Start:** `python -m uvicorn qaops.api.app:app --host 0.0.0.0 --port $PORT`
  (no `--reload`).
- **Health check:** `/health` (returns backend JSON, independent of static
  routing).

These are captured in `render.yaml` so the deployment is versioned and
reproducible. `frontend/dist` and `frontend/node_modules` are not committed;
Render builds the frontend at deploy time.

### Runtime versions

Python 3.12 and Node 22 (pinned in `render.yaml`). Phase 18 does not upgrade any
framework or dependency.

### Environment variables

Set server-side in the Render dashboard (never committed):

- `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `GEMINI_API_KEY` — provider
  credentials (set at least one for live runs). `render.yaml` declares the
  names only, with `sync: false`.
- `QAOPS_RUNTIME_DIR` — writable path for per-run workspaces (e.g.
  `/tmp/qaops-runs`).

Production configures the pipeline through `QAOPS_*` environment variables and
does not depend on a repo-root `qaops.yaml` (which is gitignored). No secret
ever appears in the frontend bundle, API responses, progress events, or
artifacts.

### Ephemeral storage

Render Free's filesystem and process memory are ephemeral. Uploaded documents,
in-memory run state, and generated artifacts are **temporary** and may disappear
after a restart, redeploy, or idle spin-down. This is acceptable for the MVP;
per-run workspace isolation is unchanged. There is no database or persistent
storage.

### Deploy steps

1. Ensure `frontend/dist` and `frontend/node_modules` are not committed.
2. Push the repository to a Git host Render can access.
3. In Render, create a Blueprint from `render.yaml` (or a Web Service using the
   build/start commands above).
4. Set `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` / `GEMINI_API_KEY` and
   `QAOPS_RUNTIME_DIR` in the service's environment.
5. Deploy, and watch the build install Python + frontend deps and run the Vite
   build.

### Post-deployment smoke test

Against `https://<service>.onrender.com`: open `/` (UI loads), `/design` and
`/runs/example` directly and refresh (SPA loads, not a 404), `/health` (backend
JSON), and `/api/v1/does-not-exist` (API 404, not HTML). Confirm the browser
console shows no routing/asset/CORS/API-base errors and that API calls go to the
same origin. A live LLM run is not required to validate deployment.

### Production-style local check

Build the frontend (`cd frontend && npm ci && npm run build`), then from the
repo root run only FastAPI:

```bash
python -m uvicorn qaops.api.app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/` and verify the same routes as the smoke test.

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
| OpenRouter model | `QAOPS_OPENROUTER_MODEL` | `openai/gpt-oss-20b:free` |
| Groq model | `QAOPS_GROQ_MODEL` | `llama-3.3-70b-versatile` |
| Execution strategy | `QAOPS_EXECUTION_STRATEGY` | `any` |

### Free-execution strategy

`QAOPS_EXECUTION_STRATEGY` controls whether recovery may use paid providers:

- `any` (default) — unrestricted; existing behaviour, unchanged.
- `free_first` — free-eligible candidates are exhausted before any paid one.
- `free_only` — only free-eligible candidates; paid providers (e.g. Anthropic)
  are never invoked.

Free eligibility is per-model, not per-provider: Groq's models and OpenRouter
`:free` models are free, Gemini's `2.5-flash` tier is free while `2.5-pro` is
paid, and Anthropic is paid. So under `free_only`, Gemini is still usable (on
its flash model) but Anthropic is skipped entirely.

**Groq** is an OpenAI-compatible free provider (set `GROQ_API_KEY`; a missing key
simply makes Groq unavailable). It has its own account quota independent of
OpenRouter, so it provides a real free failover path. OpenRouter's free daily
cap is account-wide across all `:free` models — when it is exhausted, QAOps
disables OpenRouter for the rest of the run rather than wasting calls on more
free models, while a transient per-model rate limit is retried with backoff.

### Provider reliability

QAOps chooses models by capability first and recovers from provider/model
failures within bounded budgets. In brief:

- **Capability-first eligibility.** A model must be suitable for the workload to
  be a candidate; eligibility is decided before ranking, so a large context
  window cannot rescue an unsuitable model. Cost and suitability are independent.
- **Non-text models are rejected.** Discovered models that are not text-in /
  text-out (e.g. image or music generators) cannot be selected for any pipeline
  stage.
- **Gemini dynamic discovery.** Gemini's available models are discovered live and
  fall back to curated stable aliases (`gemini-flash-latest` and friends) when
  discovery is unavailable, so a retired model ID never strands the provider.
- **Structured failure classification.** Failures are classified by scope (using
  HTTP status and provider error code, not just message text), so a transient
  rate limit, an account-wide quota exhaustion, and an unavailable model are
  handled differently.
- **Bounded recovery.** Model and provider retries/switches always stay within
  the configured budgets; discovery returning many models never causes a runaway.
- **Sanitized attempt history.** A failed run reports an ordered `attempt_history`
  of the providers/models tried and why each failed — normalized fields only,
  never keys, headers, or raw bodies.

The `ANY` / `FREE_FIRST` / `FREE_ONLY` strategies above are unchanged. See
[ADR-035](docs/adr/035-provider-reliability.md) for the architectural detail.




### Adaptive-execution bounds

When more than one provider has credentials, QAOps runs stages through an
adaptive executor that recovers from model and provider failures. Live model
discovery can surface hundreds of models per provider, so recovery is bounded
by two settings (both work in existing `qaops.yaml` files without change):

| Setting | Env var | Default | Meaning |
|---|---|---|---|
| Models per provider per stage | `QAOPS_MAX_MODELS_PER_PROVIDER_PER_STAGE` | `5` | Distinct models tried on one provider for one stage before moving to the next provider. Counts model candidates, not same-model schema retries. |
| Stage recovery attempts | `QAOPS_MAX_STAGE_RECOVERY_ATTEMPTS` | `12` | Total recovery actions (model + provider switches) for one stage before it fails cleanly. |
| Request timeout (seconds) | `QAOPS_REQUEST_TIMEOUT_SECONDS` | `60` | Deadline for one provider request. Bounds request duration; QAOps owns retries, so SDK retries are disabled and this is one attempt's wall time. |
| Provider calls per stage | `QAOPS_MAX_PROVIDER_CALLS_PER_STAGE` | `20` | Ceiling on ACTUAL provider generation calls for one stage, including structured-output repair calls. Prevents a hidden multiplier (5 models x 3 repairs = 15, under 20). |

Raising these widens the search when a model fails; lowering them fails faster.
Same-model transient retries (rate limit, timeout) are bounded separately by
`max_attempts_per_model` and do not consume the stage recovery budget. A request
that exceeds `request_timeout_seconds` is classified as a timeout and enters this
same hierarchy: retry the model, then the next model, then the next provider. A
fifth bound, `max_provider_calls_per_stage`, caps the total *actual* provider
calls per stage - including the structured-output repair calls that happen
inside one stage step - so nested retries cannot multiply the work (ADR-029,
ADR-030).

API keys come from the environment only, never config files:
`ANTHROPIC_API_KEY` for Anthropic; `GEMINI_API_KEY` (or `GOOGLE_API_KEY`) for
Gemini. Gemini support installs via the optional extra:
`pip install "qaops-ai[gemini]"` (included in `[dev]`, so CI always covers
it). Prompts are tuned against Anthropic models; judge Gemini output quality
with `scripts/evaluate_analysis.py` before relying on it (ADR-013).

Exporters render a `TestDesignResult` to JSON (canonical), Markdown, CSV, and
Excel. JSON, Markdown, and CSV need no extra dependency; Excel installs via
`pip install "qaops-ai[excel]"` (included in `[dev]`). All output is
deterministic and derives from the JSON serialization (ADR-016).

## Roadmap

| Phase | Deliverable | Status |
|---|---|---|
| 0 | Skeleton, domain models, protocols, config, CI | ✅ |
| 1 | LLM abstraction: Anthropic + mock clients, structured-output retry | ✅ |
| 2 | Requirement Analyzer, Business Rule Extractor, Gap Report | ✅ |
| 3 | Scenario Generator (BVA, EP, negative, RBAC, state transitions) | ✅ |
| 4 | Test Case Generator | ✅ |
| 5 | Coverage Validator, Traceability Matrix, Deduplicator | ✅ |
| 6 | Exporters (JSON, Markdown, CSV, Excel) | ✅ |
| 7 | CLI (`qaops design`), qaops.yaml config, docs | ✅ |
| 8 | Document ingestion (text/markdown, PDF; DOCX/HTML stubs) | ✅ |

**V1 non-goals:** automation code generation (Selenium/Playwright/etc.), test execution, defect analysis, docx/PDF ingestion, persistence, web UI, semantic deduplication.
