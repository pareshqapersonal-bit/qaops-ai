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
| [036](036-exhaustive-test-design.md) | Exhaustive, evidence-bound test design via test conditions (REQ->BR->SC->COND->TC) | Accepted |
| [037](037-condition-expansion-and-ambiguity-integrity.md) | Condition expansion & ambiguity integrity: technique-driven derivation, deterministic gap->unresolved linkage | Accepted |
| [038](038-technique-driven-expansion.md) | Technique-driven test-case expansion: deterministic ExpansionPlanner turns each condition's technique into bounded variant slots | Accepted |
| [039](039-docx-ingestion.md) | DOCX ingestion via the existing loader abstraction (python-docx, [docx] extra) | Accepted |
| [040](040-execution-checkpointing-and-resume.md) | Execution checkpointing, partial artifacts, and in-process resume | Accepted |
| [041](041-orchestrator-agent.md) | The Orchestrator Agent: QAOps' first agentic capability (plan / decide / reflect, artifacts stay pipeline-owned) | Accepted |
| [042](042-goal-driven-agent-loop.md) | The goal-driven agent loop: observe → decide → act → reflect, bounded resume, artifacts stay pipeline-owned | Accepted |
| [043](043-multi-agent-supervisor-refactor.md) | Multi-agent supervisor refactor: OrchestratorAgent decomposed into a supervisor coordinating PlanningAgent/ExecutionAgent/ReflectionAgent, byte-identical behaviour | Accepted |
| [044](044-evidence-first-unresolved-classification.md) | Narrow gap propagation: a gap unresolves a condition only on subject-matter overlap, not shared requirement — fixes false-positive "confirm with PO" placeholders | Accepted |
| [045](045-deterministic-quality-review.md) | Deterministic QualityReviewer (not an Agent) consuming CoverageReport to produce an advisory ReviewReport; LLM ReviewAgent deferred | Accepted |
| [046](046-review-agent-advisory-narrative.md) | Advisory ReviewAgent consuming ReviewReport to produce ReviewAdvice (prioritized explanations + recommendations); Runner-invoked, gated OFF by default, deterministic fallback | Accepted |
| [047](047-jira-style-ticket-input.md) | Jira-style ticket input: deterministic TicketNormalizer → Markdown → existing DOCUMENT pipeline via a shared run-creation helper; no second pipeline, no Jira integration | Accepted |
| [048](048-test-case-assumption-provenance.md) | Test-case assumption provenance: additive TestCase.assumptions + generator prompt contract separating source-backed / QA test data / unsupported assumptions; byte-identical for evidence-complete cases | Accepted |
| [049](049-test-case-assumptions-review-finding.md) | Threshold-gated deterministic QualityReviewer finding (test_case_assumptions, WARNING/completeness, 50%) surfacing Phase 33 assumptions with exact TestCase references; quantity-based severity, no prose classification; ReviewAgent/CoverageValidator/Phase 33 unchanged | Accepted |
| [050](050-ticket-design-reference-attachment.md) | Optional design/reference attachment on a ticket: multipart endpoint, extracted via load_document and appended as a verbatim evidence section into one combined document; ticket-only stays compatible; no second pipeline | Accepted |
| [051](051-multiple-ticket-attachments.md) | Multiple ticket attachments (field name `attachment` kept, cardinality->list): each extracted via load_document and appended as an ordered evidence section into one combined document; strict-fail on any bad file; XLSX/images deferred; single/no-attachment stays 35A-compatible | Accepted |
| [052](052-visual-evidence-transport-seam.md) | Visual evidence transport seam (Phase 36 Part 1): additive LLMMessage.images + ImagePart + internal EvidencePackage, wired to the analyzer via run_structured_stage; hard-fail on images without a multimodal provider; no provider/OCR/UI, text-only byte-identical | Accepted |
| [054](054-image-aware-provider-selection.md) | NVIDIA registered in the execution registry (QAOPS_PROVIDER=nvidia honored) + image-aware provider selection: image runs only consider image-capable providers and fail fast with a clear message when none exists; text-only runs unchanged | Accepted |
| [055](055-nvidia-free-classification.md) | Classify NVIDIA/Nemotron as free (cost-based) so image runs stay eligible under QAOPS_EXECUTION_STRATEGY=free_only; priority stays 60 so PRD/text ordering is unchanged. Caveat: NVIDIA free tier is rate-limited and dev/eval-only | Accepted |
| [056](056-gap-null-sentinel-normalization.md) | gap_analyzer normalizes null-sentinel requirement IDs ("null"/"none"/""/whitespace -> None) before validation, so a model emitting the string "null" no longer stalls the pipeline; real unknown IDs still fail; provider selection unchanged | Accepted |

ADR format: Context → Decision → Consequences. A superseding ADR must link back to the ADR it replaces.
