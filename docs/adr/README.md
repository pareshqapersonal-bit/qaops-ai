# Architecture Decision Records

| ADR | Title | Status |
|---|---|---|
| [001](001-llm-generates-code-validates.md) | LLM generates, code validates | Accepted |
| [002](002-single-llm-boundary.md) | Single LLM boundary behind an `LLMClient` interface | Accepted |
| [003](003-typed-models-between-stages.md) | Strict typed Pydantic models between all pipeline stages | Accepted |
| [004](004-python-312-floor.md) | Python 3.12 minimum, not 3.14 | Accepted |
| [005](005-defer-agent-registry-and-storage.md) | Defer plugin registry and storage; protocols only | Accepted |
| [006](006-text-input-only-v1.md) | Plain text / Markdown input only in V1 | Accepted |
| [007](007-heuristic-deduplication.md) | Heuristic deduplication that flags, never deletes | Accepted |
| [008](008-testing-strategy.md) | Testing strategy for non-deterministic components | Accepted |
| [009](009-configuration.md) | Configuration via pydantic-settings, constructor injection | Accepted |
| [010](010-prompt-templating.md) | Prompt templates use `string.Template`, not `str.format` | Accepted |
| [011](011-wire-schemas.md) | Wire schemas are separate from domain models | Accepted |
| [012](012-duplicate-policy.md) | Generation-time duplicates fail loudly; near-duplicates flag later | Accepted |
| [013](013-second-provider-gemini.md) | Second provider (Gemini) via factory selection and optional extra | Accepted |
| [014](014-test-case-mapping.md) | Flat test-case wire schema, code-assigned step numbers, per-scenario reference scoping | Accepted |
| [015](015-deterministic-validation.md) | Validation stage is deterministic, with no LLM in its signature | Accepted |
| [016](016-exporter-framework.md) | JSON is canonical; other exporters derive from it; CSV is intentionally lossy | Accepted |
| [017](017-cli-composition-root.md) | The CLI is a thin composition root over existing components | Accepted |
| [018](018-document-ingestion.md) | A DocumentLoader ingestion layer, not per-format branching | Accepted |
| [019](019-evaluation-mode.md) | A temporary evaluation mode, pending document chunking | Accepted (temporary) |
| [020](020-document-chunking.md) | Chunking is internal to requirement analysis, invisible downstream | Accepted |
| [021](021-adaptive-chunking.md) | Chunk sizing is adaptive, decided by strategy not configuration | Accepted |
| [022](022-multi-entry-pipeline.md) | Multiple entry points by composing existing stages | Accepted |
| [023](023-workflow-safety.md) | Fail safely: never destroy input, never surface raw provider errors | Accepted |
| [024](024-structured-scenario-readers.md) | Structured readers for human-authored scenarios; prose stays with the analyzer | Accepted |
| [025](025-workflow-detection.md) | Workflow detection is deterministic, and biased toward the document route | Accepted |
| [026](026-adaptive-execution.md) | Provider failover by rebuilding remaining stages, not by mutating them | Accepted |
| [027](027-model-discovery.md) | Exhaust models within a provider before switching, and discover them at runtime | Accepted |
| [028](028-fastapi-backend.md) | A FastAPI interface over a shared DesignService, not a second pipeline | Accepted |
| [029](029-bounded-execution.md) | Bounded, ranked candidate selection and structured execution progress | Accepted |
| [030](030-request-timeout.md) | A per-request deadline with QAOps-owned retries, and unambiguous progress | Accepted |
| [032](032-frontend-architecture.md) | A thin React frontend over the existing API, typed from the real contract | Accepted |
| [033](033-production-deployment.md) | Single-service production deployment on Render (FastAPI serves the Vite build) | Accepted |
| [034](034-free-provider-expansion.md) | Free-capacity expansion: Groq, free-execution strategy, OpenRouter provider-wide quota | Accepted |
| [035](035-provider-reliability.md) | Provider reliability: capability-first model eligibility, Gemini discovery, structured failure classification, attempt-history observability | Accepted |

ADR format: Context → Decision → Consequences. A superseding ADR must link back to the ADR it replaces.
