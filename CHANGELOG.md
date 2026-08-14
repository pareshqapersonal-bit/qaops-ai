# Changelog

All notable changes to QAOps AI are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Pre-1.0, minor versions may contain breaking changes; each is called out explicitly.

## [0.10.0-alpha] - 2026-07-21

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

## [0.18.0-dev] - unreleased

### Phase 40B: per-stage provider selection for image runs

Fixes image runs failing when NVIDIA's free endpoint 500s on downstream stages
(ADR-057). Phase 38 made provider selection image-aware at the RUN level, forcing every
stage onto NVIDIA; but only requirement_analyzer consumes images, so downstream text
stages were needlessly pinned to the flaky image provider with no eligible failover.

- **Per-stage selection**: the image-consuming stage (requirement_analyzer) requires an
  image-capable provider; downstream stages of an image run EXCLUDE NVIDIA
  (`exclude_image_providers`) and use the normal text chain. The image stage is named
  by the orchestration layer (`DesignService` passes `image_stage_name` + ordered
  `stage_names`) - no hard-coded index, no stage-protocol or RequirementAnalyzer change.
- **Image run result**: requirement_analyzer -> NVIDIA (image byte-identical);
  business_rule_extractor / gap_analyzer / scenario_generator / test_case_generator /
  coverage -> Groq/Gemini. Downstream NVIDIA 500s can no longer block the pipeline.
- **Recovery**: image stage recovers only to image-capable providers (Phase 36A
  hard-fail preserved); downstream recovers across the text chain; NVIDIA never a
  downstream candidate.
- **Resume fix**: resuming at a downstream stage no longer requires NVIDIA merely
  because the original run had images.
- **Unchanged**: text/PRD runs (no image stage -> identical selection, ordering,
  fallback; NVIDIA stays low-priority, not preferred); free_only (analyzer->NVIDIA,
  downstream->free text chain); budgets/health accounting; ImagePart, EvidencePackage,
  sidecar, RequirementAnalyzer, pipeline stages, LLM abstraction, provider clients,
  image ingestion, execute_run, API, frontend; Phase 40A gaps.py.
- **Tests**: +12 Phase 40B (per-stage selection, downstream NVIDIA exclusion across all
  stages, image-stage fail-fast, downstream text recovery, resume at downstream stages,
  end-to-end image run analyzer=NVIDIA/downstream=text with no image propagation
  downstream, text run doesn't prefer NVIDIA). Phase 38/39 executor tests migrated to
  the per-stage API. ADR-057. Version stays `0.18.0-dev`.

### Phase 40A: gap_analyzer tolerates null-sentinel requirement IDs

Fixes a production image run stalling at `gap_analyzer` with "Model referenced unknown
requirement IDs: ['null']" (ADR-056). The gap's `requirement_id` is nullable by design,
but Nemotron emitted the STRING `"null"` instead of JSON null, which passed the
`is not None` guard and failed as an unknown ID - blocking all downstream stages.

- **Null-sentinel normalization**: before ID validation, `gap_analyzer` coerces
  `"null"`, `"none"`, and empty/whitespace-only strings (case-insensitive) to real
  None via a small helper. Any other string (including `"REQ-999"`, `"abc"`) is left
  intact and still fails validation. Provider-agnostic robustness; benefits any model.
- **No provider-selection change**: only `gaps.py` is touched. Phase 38 image-aware
  selection, Phase 39 NVIDIA free classification, provider priority (nvidia stays 60,
  not preferred for text), ordering, and `QAOPS_EXECUTION_STRATEGY=free_only` are all
  unchanged.
- **Not in scope**: per-stage provider selection (Phase 40B/Shape-3) remains deferred.
- **Tests**: +21 Phase 40A (helper across all sentinel/valid/unknown cases; through
  the real GapAnalyzer stage: real null accepted, `"null"`/`" NULL "`/`"none"`/`""`/
  whitespace normalized, valid REQ IDs unchanged, unknown IDs still fail, the exact
  production failure case now passes, mixed valid+null). ADR-056. Version stays
  `0.18.0-dev`.

### Phase 39: NVIDIA classified free (image runs work under FREE_ONLY)

Fixes image tickets failing under `QAOPS_EXECUTION_STRATEGY=free_only` while
PRD/document runs worked (ADR-055). NVIDIA is the only image-capable provider, but
`_configured_model_is_free("nvidia")` fell through to False, so the FREE_ONLY strategy
filter removed NVIDIA before image-capability selection - leaving no image-capable
candidate.

- **NVIDIA classified free** (cost-based): a `provider == "nvidia"` branch in
  `_configured_model_is_free` returns True. NVIDIA's Nemotron endpoint has no per-call
  cost through build.nvidia.com's OpenAI-compatible API, consistent with how gemini
  flash / groq (also rate-limited) are already `free=True`.
- **CAVEAT (documented, not hidden)**: NVIDIA's free hosted tier is RATE-LIMITED
  (~40 RPM, credits can exhaust -> HTTP 429) and NVIDIA's FAQ restricts it to
  development/evaluation, not production traffic. "free" means zero monetary cost per
  call - not unlimited throughput or a production SLA.
- **PRD/text ordering unchanged**: NVIDIA keeps `priority=60`, behind the existing
  free providers (groq=10, gemini), so it is never preferred for text runs and the
  order among existing providers is identical with or without NVIDIA.
- **Unchanged**: provider priority, the strategy engine, the selector, image
  transport/sidecar/provider implementation, Render config, and PRD/document behavior.
- **Tests**: +8 Phase 39 (nvidia free classification, free_only+image selects NVIDIA,
  no fallback to text-only, text ordering unaffected, byte-identical transport under
  free_only). ADR-055. Version stays `0.18.0-dev`.

### Phase 38: NVIDIA registry registration + image-aware provider selection

Fixes image tickets failing in production with "all providers failed / gemini"
despite `QAOPS_PROVIDER=nvidia` (ADR-054). Two execution-layer defects: NVIDIA was
missing from the execution registry (so it was silently ignored), and provider
selection was not image-aware (so image runs could recover onto text-only providers).

- **NVIDIA registered** in `qaops/execution/registry.py` (`_IMPLEMENTED` + a
  `ProviderInfo` with `key_variables=("NVIDIA_API_KEY",)`, `images=True`,
  `priority=60`). `QAOPS_PROVIDER=nvidia` now leads the chain and NVIDIA appears in
  `available_providers()` when `NVIDIA_API_KEY` is set.
- **Image-aware selection**: `ProviderInfo.images` populates
  `ModelInfo.images_supported`; `StageRequirements.needs_images` + a `_passes_filter`
  clause exclude non-image-capable candidates for image-bearing runs.
- **Run-level requirement**: `DesignService._execute` derives `requires_images` from
  the Phase 36B evidence and passes it to `AdaptiveExecutor`; no per-stage plumbing.
- **Fail fast, clearly**: an image run with no image-capable provider raises a clear
  `StageError` at selection ("run includes image evidence… set QAOPS_PROVIDER=nvidia")
  before any provider call - no fallback to text-only providers, no silent drop.
- **Unchanged**: text-only runs (`needs_images=False` is a no-op, so selection and
  multi-provider fallback are byte-identical); the PRD/document flow; `ImagePart`,
  `EvidencePackage`, `LLMMessage`, `RequirementAnalyzer`, `structured.py`,
  `execute_run`, the evidence sidecar, and pipeline stages.
- **Tests**: +16 Phase 38 (registry gap fix, image-capability filtering, executor
  selection scenarios, no-capable-provider clear failure, and an end-to-end mocked
  image ticket selecting NVIDIA with transport reaching the client). Phase 36B
  transport tests updated to run image cases under `provider="nvidia"`.
- **ADR-054**. Version stays `0.18.0-dev`.

### Phase 36 Part 1: Visual evidence transport seam

Introduces the multimodal transport for visual evidence and wires it to the
RequirementAnalyzer, without adding any provider that consumes images (ADR-052).
Plumbing only; text-only behavior is unchanged.

- **`ImagePart`** (`qaops/llm/models.py`): provider-agnostic image value - media_type
  (png/jpeg), base64 data, source_filename, order, optional page/image_index.
- **`LLMMessage.images: list[ImagePart] = []`**: additive, optional; `content: str`
  unchanged. A text-only message serializes with no `images` key (byte-identical).
- **`EvidencePackage`** (`qaops/ingestion/evidence.py`): internal ingestion/API value
  object carrying images with provenance/order; travels alongside `RequirementInput`
  (which is NOT modified) and is consumed only by the analyzer. Not a domain model.
- **`run_structured_stage`** gains an optional `images` parameter attached to the
  single user message; only the analyzer passes it, so all other stages build
  byte-identical requests.
- **`RequirementAnalyzer.run(data, evidence=None)`**: passes ordered images through the
  seam; the default keeps the existing call backward-compatible.
- **Hard-fail, never silent drop**: `generate_structured` raises `LLMProviderError` if
  a request carries images but the provider does not declare image support (read via a
  `supports_images` `getattr` convention, so no provider is modified).
- **Out of scope (deferred)**: no real provider (no Nemotron/Anthropic/Gemini vision),
  no OCR, no image-attachment ingestion, no image UI, no PDF/DOCX loader changes, no
  embedded-image extraction, no pipeline-topology change.
- **Unchanged**: all five downstream stages, CoverageValidator, QualityReviewer,
  ReviewAgent, all LLM providers, `RequirementInput`, Phase 33/34, and Phase 35A/35B.
- **Tests**: backend 936 passed (+19 Phase 36: ImagePart validation, EvidencePackage
  construction/ordering, text-only byte-identity, analyzer-attaches-images,
  downstream-receives-none, Mock records image requests, text-only-provider hard-fail).
- **ADR-052**. Version stays `0.18.0-dev`.

### Phase 35B: Multiple ticket attachments

Extends the single ticket attachment (Phase 35A) to multiple, folded into one
combined document as ordered evidence (ADR-051).

- **Multiple attachments**: the ticket endpoint's multipart `attachment` field now
  accepts 0, 1, or many files. The field name is unchanged (repeated once per file,
  matching the backend `list[UploadFile]`) - no breaking `attachments` rename.
- **Supported formats unchanged**: PDF, DOCX, MD, MARKDOWN, TXT. XLSX and images
  remain deferred (no spreadsheet loader, no OCR, no multimodal, no LLM change).
- **AttachmentEvidence**: a small internal frozen dataclass (filename, text) in the
  ingestion/API layer - not a pipeline/domain model, never imported by any stage.
- **Ordered evidence**: `append_reference_materials` emits one
  `## Design / Reference Material` section per attachment, in upload order, verbatim;
  never sorted or de-duplicated. The Phase 35A singular helper is now a thin wrapper,
  so single-attachment output is byte-identical.
- **Strict failure**: if ANY attachment fails validation/extraction (unsupported
  suffix, empty, loader failure incl. missing dependency, or no extractable text),
  the whole request returns 400 naming the file - never a silent skip, never a
  partial run, never a 500. No run is created on failure. Empty description still 422.
- **Provenance**: `source_name` stays ticket-anchored; each attachment's filename
  appears only in its evidence section's `Source:` line.
- **Frontend**: the ticket attachment input accepts multiple files (`multiple`);
  `createTicketRun` sends each under the `attachment` field in order.
- **Unchanged**: ticket-only (byte-identical to Phase 32), single attachment
  (byte-identical to Phase 35A), the existing `/api/v1/design` document endpoint,
  `_create_and_schedule_run`, `execute_run`, `load_document`, `classify_input`, all
  pipeline stages, CoverageValidator, QualityReviewer, ReviewAgent, the LLM
  abstraction/providers, Phase 33/34, and the Phase 31 `review_advice_enabled` flag.
- **Tests**: backend 917 passed (+17 Phase 35B); frontend 68 passed (+multi-file
  ticket test). Phase 32/35A tests remain green unchanged.
- **ADR-051**. Version stays `0.18.0-dev`.

### Phase 35: Ticket design/reference attachment

Lets a Jira-style ticket optionally carry one design/reference file as additional
evidence, combined into a single document for the existing pipeline (ADR-050).

- **Multipart ticket endpoint**: `POST /api/v1/design/ticket` now accepts multipart
  form data - ticket fields plus an optional `attachment`. Still delegates to the
  unchanged `_create_and_schedule_run`; no second pipeline, no Jira integration.
- **Attachment as evidence**: the file is extracted with the existing
  `load_document` and appended verbatim to the ticket Markdown as a delimited
  section:
  ```
  ## Design / Reference Material
  Source: <filename>

  <extracted attachment text>
  ```
  The existing analyzers decide what is a requirement - the attachment is never
  parsed into requirements at the endpoint.
- **Supported attachment formats**: PDF, DOCX, MD/Markdown, TXT (a narrower set than
  the full document-upload formats; expandable later).
- **Empty acceptance criteria now omit the section** entirely (previously a bare
  `## Acceptance Criteria` heading) - an intentional normalization change, pinned by
  tests. `TicketRequest.acceptance_criteria` stays optional in the schema.
- **Error behavior (all 400, never 500)**: unsupported suffix, empty attachment,
  loader failure (incl. missing pdf/docx dependency, surfaced as a clear message),
  and no-extractable-text each return 400. Empty description still returns 422.
- **Provenance**: `source_name` stays ticket-anchored; the attachment filename is
  recorded only in the evidence section's `Source:` line.
- **Frontend**: the ticket form drops the Acceptance Criteria field and adds an
  optional "Design / Reference Material" file input (PDF/DOCX/MD/TXT);
  `createTicketRun` sends multipart FormData. The document-upload UI is unchanged.
- **Unchanged**: ticket-only runs (byte-identical pipeline path for equivalent
  normalized input), the existing `/api/v1/design` document endpoint, the Phase 31
  `review_advice_enabled` flag (`QAOPS_REVIEW_ADVICE_ENABLED`, still opt-in),
  ReviewAgent, ReviewReport, CoverageValidator, Phase 33 assumptions, and the
  Phase 34 finding/threshold.
- **Tests**: backend 900 passed (+18 Phase 35; Phase 32 API tests updated to the
  multipart contract); frontend 67 passed (+ticket attachment/AC-removal tests).
- **ADR-050**. Version stays `0.18.0-dev`.

### Phase 34: Test-case assumptions review finding

Surfaces Phase 33's `TestCase.assumptions` on the review surface so a QA lead can
act on them, without unsafe interpretation of the free-text strings (ADR-049).

- **New QualityReviewer finding** `test_case_assumptions` (`WARNING`,
  `completeness`): fires when the fraction of test cases carrying at least one
  assumption reaches the threshold (`_ASSUMPTION_WARNING_RATIO = 0.50`). Consumes
  Phase 33's `TestCase.assumptions`; deterministic.
- **Quantity-based severity, no prose classification**: severity comes from HOW
  MANY cases depend on unconfirmed facts, never from interpreting the assumption
  text (which could be setup, product capability, or business-rule assumptions -
  indistinguishable from wording). The reviewer treats each assumption as opaque.
- **Traceability**: `references` are the exact `TestCase` IDs of assumption-bearing
  cases, sorted, verbatim; the assumption text is never echoed or categorized.
- **Readiness**: only in aggregate and only when the finding fires, via the existing
  ReviewAgent warning mechanism - no new readiness logic.
- **Unchanged**: ReviewAgent (finding-agnostic, surfaces the new finding
  automatically), CoverageValidator, `provisional` status, and all of Phase 33.
- **Calibration**: exercised through the real pipeline (a ticket-sourced run with
  2/3 cases carrying assumptions = 67% produced the WARNING with references
  [TC-001, TC-002]); the assumption strings were realistic sparse-ticket text
  because this environment has no live LLM key - the finding mechanics/ratio/
  references are genuine pipeline output. 50% accepted as a conservative default
  (mirrors `high_provisional_ratio`), tunable via one constant.
- **Backward compatibility**: runs with no assumptions produce a `ReviewReport`
  identical to pre-Phase-34 (the finding is absent), pinned by a regression test.
- **Tests**: backend 882 passed (+8 Phase 34: threshold fire/silent, exact sorted
  references, text-not-interpreted, deterministic, provisional-untouched, pinned
  byte-identical no-assumptions regression); frontend 65 unchanged.
- **ADR-049**. Version stays `0.18.0-dev`.

### Phase 33: Test-case assumption provenance

Makes unsupported assumptions in generated test cases visible and separable from
legitimate QA-generated test data, without prohibiting either (ADR-048).

- **`TestCase.assumptions: list[str]`** (default empty): product/system facts a
  case must assume that the source (ticket/requirements/business rules/evidence-
  bound conditions) does not establish. QA test data stays in `test_data`;
  evidence-backed behaviour stays traceable via the condition's `source_basis`.
- **Generator prompt contract** (`test_case_generator_v1.md`, edited in place -
  additive guidance): names the three categories (source-backed / QA test data /
  unsupported assumption), keeps chosen values in `test_data` and forbids phrasing
  them as product rules, and routes any required-but-unsupported fact into
  `assumptions` instead of silently into `preconditions`/`expected_result`.
- **Threading**: the field flows from the wire extraction model into `TestCase`,
  defaulting empty when absent.
- **Backward compatibility (proven, not assumed)**: a pinned regression test
  asserts an evidence-complete case serializes with NO `assumptions` key under
  `exclude_defaults`, so existing document runs are byte-identical. One Phase 30
  non-mutation test was corrected to compare before/after of the object under
  review (robust to additive fields) rather than against a pre-Phase-33 fixture;
  the reviewer was confirmed to not mutate the result.
- **Out of scope**: no change to requirements, business rules, gaps, scenarios, the
  TestCondition evidence model, CoverageValidator, or pipeline topology; the
  QualityReviewer and ReviewAgent do NOT consume `assumptions` this phase.
- **Tests**: backend 874 passed (+5 Phase 33: default-empty, pinned byte-identical
  regression, assumptions flow-through, full-serialization presence, coverage-
  unaffected); frontend 65 unchanged. Phase 25-32 green.
- **ADR-048**. Version stays `0.18.0-dev`.

### Phase 32A: Jira-style ticket input

Accepts a Jira-style ticket and runs it through the EXISTING document pipeline to
produce the same artifacts (requirements → … → coverage, ReviewReport, and
ReviewAdvice when enabled). No second pipeline, no Jira integration (ADR-047).

- **`TicketNormalizer`** (`qaops/ingestion/ticket_normalizer.py`): a pure,
  deterministic transcription of a ticket into Markdown - title, optional
  `Ticket:/Priority:/Labels:` header (only when supplied), verbatim `## Description`,
  and a verbatim numbered `## Acceptance Criteria` list. Never invents requirements,
  rules, expected values, or criteria; never rewrites; emits no table/scenario
  marker so it stays on the DOCUMENT route. No LLM.
- **`TicketRequest`** (request-only schema): title/description required;
  `acceptance_criteria` list with empty allowed; optional ticket_id/priority/labels.
- **Shared run-creation helper** `_create_and_schedule_run` extracted verbatim from
  `submit_design`; both the document upload and the ticket endpoint delegate to it -
  no duplicated run creation/persistence/scheduling/response. Document behaviour
  unchanged (the two upload-specific 400 checks stay in `submit_design`).
- **`POST /api/v1/design/ticket`**: validates, normalizes, and runs the ticket
  through the same execute_run/DOCUMENT path. Provenance rides `source_name` as
  `"<TICKET-ID> - <title>"` (or title alone) via the `.md` filename; generated
  REQ-*/BR-*/SC-*/TC- ids untouched.
- **Frontend**: an additive Document/Ticket selector on New Run with a minimal
  ticket form; existing document upload unchanged.
- **Scope**: ticket payload only - no Jira auth, REST, retrieval, updates, issue
  creation, publishing, or sync; no new agent, no ticket-specific stage.
- **Tests**: backend 869 passed (+25 Phase 32: normalizer determinism/verbatim/no-
  fabrication, TicketRequest validation, endpoint, provenance, shared helper,
  DOCUMENT routing, ticket→pipeline integration, sparse→gap, document regression);
  frontend 65 passed (+3 ticket-mode). One OTP-login fixture. Phase 25-31 green.
- **ADR-047**. Version stays `0.18.0-dev`.

### Phase 31: Advisory ReviewAgent (ReviewAdvice over ReviewReport)

Introduces the LLM-optional `ReviewAgent` reserved by ADR-045. It consumes the
deterministic `ReviewReport` and produces advisory `ReviewAdvice` (prioritized
explanations + consolidated recommendations) for a QA lead. First deliberately
non-deterministic output in the product, so it is strictly opt-in (ADR-046).

- **`ReviewAgent`** (`qaops/agent/agents/review.py`, subclass of the `Agent` ABC):
  consumes the `ReviewReport` and NOTHING else, so it structurally cannot
  recompute anything. Never creates findings, changes severity/references, mutates
  artifacts, affects execution or loop decisions, or feeds back into generation.
- **Deterministic by default**: `advise()` always builds a complete `ReviewAdvice`
  from the report (findings prioritized critical -> warning -> info, consolidated
  de-duplicated recommendations, severity-count headline). An optional best-effort
  LLM pass refines ONLY prose (headline, per-item explanation), mapped back by
  finding `code`; unknown codes ignored; severity/references never change; any
  failure falls back to deterministic. `generated_by` records provenance.
- **New models** `ReviewAdvice` / `ReviewAdviceItem`; new prompt
  `agent_review_advice_v1.md`.
- **Setting-gated, OFF by default** (`review_advice_enabled=False`): with it
  disabled, no `review_advice` field and no export - runs are byte-identical to
  Phase 30. Verified.
- **Runner-invoked**, COMPLETED runs only, after the `QualityReviewer`, on both
  fresh and resume-completed paths. `SupervisorAgent` unchanged.
- **Additive surfacing**: optional `review_advice` field on the run-status
  response (defaulted `None`) and standalone `review_advice.json` export.
- **Tests**: backend 844 passed (+13 Phase 31: deterministic advice, prioritization,
  LLM enrichment + immutability guarantees, fallback, gating OFF/ON, report
  non-mutation); frontend 62 unchanged; Phase 30 tests unchanged (backward compat).
- **ADR-046**. Version stays `0.18.0-dev`.

### Phase 30: Deterministic Quality Review layer (QualityReviewer)

Introduces an independent, deterministic quality review of a completed run's
artifacts. Advisory only, read-only, and outside the pipeline and the supervisor;
all Phase 25-29 guarantees preserved (ADR-045).

- **New `QualityReviewer`** (`qaops/review/`): a deterministic, LLM-free, non-Agent
  analyzer. `review(TestDesignResult) -> ReviewReport`. It **consumes** the
  existing `CoverageReport` (metrics, `uncovered_*`, `duplicate_pairs`,
  `invalid_references`) and adds only net-new checks (empty scenarios, provisional
  cases, truncation, unresolved-ratio thresholds). Same input -> same output;
  never mutates the result, generates no artifact, invokes no stage/loop, writes
  no checkpoint.
- **New models**: `ReviewReport`, `ReviewFinding`, and `ReviewSeverity` /
  `ReviewCategory` enums. Findings carry severity/category/message and reference
  artifact ids (never copy artifact content).
- **Advisory only**: findings never gate, fail, or downgrade a run. A run with
  CRITICAL findings is still COMPLETED. Verified: Auto-Delete baseline flags
  CRITICAL (20/22 unresolved), BOGO baseline stays INFO (4/11).
- **Runner-invoked, COMPLETED only**, after the SupervisorAgent returns - the
  Phase 28 supervisor architecture is unchanged. Any review failure degrades to
  "no review" without affecting run status.
- **Additive surfacing**: optional `review` field on the run-status response
  (defaulted `None`, backward compatible) and a standalone `review_report.json`
  export listed among run artifacts. Never merged into `TestDesignResult` or
  `CoverageReport`.
- **v2 deeper findings** (same architecture, additive): blocker/major gaps from
  `gap_report` (CRITICAL/WARNING), partial requirements via
  `RequirementCoverage.missing_categories`, high provisional ratio, priority-
  distribution skew, and positive-only-suite detection (missing negative/boundary)
  - the last two under a new `QUALITY` category. Priority/test-type distributions
  added as observations. A proposed "traceability holes" check was dropped (would
  duplicate `uncovered_requirements` or never fire). Only `reviewer.py` and the
  `QUALITY` enum member changed; no model/API/schema change.
- **Future**: an LLM `ReviewAgent` will consume this `ReviewReport` to generate
  explanations/recommendations - it will read findings, never recompute them.
- **Bugfix**: the deterministic review was only wired on the fresh-run COMPLETED
  path; a run resumed to completion produced no review. The `resume_run` COMPLETED
  path now invokes the same `_build_review` (review field + standalone export),
  symmetric with `execute_run`. `QualityReviewer` logic unchanged. Regression test
  added (failed -> resume -> COMPLETED -> review present).
- **Tests**: backend 831 passed (+33 Phase 30 incl v2 + resume-review regression:
  reviewer determinism/non-mutation, per-check findings, two-fixture baselines,
  API surfacing + backward compat, resume produces review); frontend 62 unchanged.
- **ADR-045**. Version stays `0.18.0-dev`.

### Phase 29: Narrow gap propagation in unresolved classification (artifact quality)

Fixes a defect where a well-specified PRD produced 20/22 conditions `unresolved`
(nearly every test case carried a "confirm with the product owner" placeholder).
Root cause, proven from the failing run's artifacts: each requirement-level gap
was propagating to **every** condition on that requirement, including conditions
verifying a different, fully-specified aspect. The fix narrows propagation to
subject-matter overlap without eliminating it (ADR-044).

- **Strengthened** the `TestConditionAnalyzer` prompt (the fix): a gap makes a
  condition `unresolved` only when the gap's missing information is the very thing
  that condition verifies; sharing a requirement is not sufficient. A guard rail
  keeps genuinely gap-affected conditions unresolved (narrowing must not become
  ignoring or fabrication).
- **Deterministic backstop unchanged**: `_apply_gap_linkage`/`_blocking_gap` are
  left as-is. The production failure originated in the LLM's classification, so
  the change is confined to the prompt - the smallest surface supported by
  evidence. A targeted backstop change will be considered only if a post-fix
  re-run of the PRD shows it still propagating gaps incorrectly.
- **Fixtures**: the two real run artifacts are checked into
  `tests/fixtures/phase29` (failing Auto-Delete 20/22, healthy BOGO 4/11) as the
  baseline for validating the prompt fix by re-running the pipeline.
- **No interface change**: no API field, no schema change, no UI change.
- **Tests**: backend 796 passed (+6 Phase 29: prompt contract + fixture
  baselines); frontend 62 unchanged.
- **ADR-044**. Version stays `0.18.0-dev`.

### Phase 28: Multi-agent supervisor refactor (pure architectural evolution)

Decomposes the monolithic `OrchestratorAgent` into a `SupervisorAgent`
coordinating three specialized agents, with 100% identical external behaviour.
Zero new QA functionality — the deliverable is a cleaner architecture that is
open to future agents (ADR-043).

- **Added** `qaops/agent/agents/` with `PlanningAgent` (wraps `ExecutionPlanner`),
  `ExecutionAgent` (delegates only to `DesignService.run()`/`.resume()`, never
  executes a stage), and `ReflectionAgent` (wraps `Reflector`).
- **Added** `qaops/agent/supervisor.py` — `SupervisorAgent` composes the three
  agents and drives the `GoalDrivenLoop`; it is the coordination layer the runner
  now talks to.
- **`GoalDrivenLoop` kept as a reusable engine** — its logic is unchanged; it now
  drives acts through the `ExecutionAgent` (a pass-through to `DesignService`), so
  behaviour is byte-identical.
- **`OrchestratorAgent` kept as a thin backward-compatible facade** delegating to
  the supervisor; existing callers and tests work unchanged.
- **`observe()`/`decide()` stay utility functions** (not agents) — exactly three
  specialized agents, no over-abstraction.
- **Behavioural identity proven**: all 780 Phase 25–27 tests pass unchanged, plus
  a Phase 28 suite (+10) asserting SupervisorAgent vs OrchestratorAgent-facade vs
  direct `DesignService` produce byte-identical artifacts, identical checkpoints,
  and identical loop summaries. No API/UI schema change; the frontend suite passes
  untouched (identical API responses).
- **Preserved**: pipeline sole artifact generator; `AdaptiveExecutor` retry/
  failover; `CheckpointStore` checkpoint ownership; `DesignService` execution
  ownership; deterministic execution; backward compatibility.
- **Tests**: backend 790 passed (+10); frontend 62 passed (unchanged).
- **ADR-043**. Version stays `0.18.0-dev`.

### Phase 27: Goal-driven agent loop (observe → decide → act → reflect)

Evolves the Phase-26 single-shot orchestrator into a bounded goal-driven loop
that manages execution until a terminal condition, delegating every act to the
unchanged deterministic pipeline. The agent gains a decision lifecycle; it still
authors no QA artifact, executes no stage, and writes no checkpoint (ADR-042).

- **Added** `qaops/agent/observe.py` (read-only `Observation`) and
  `qaops/agent/loop.py` (`GoalDrivenLoop` + deterministic `decide()`): observe
  execution state → decide continue/resume/stop/recommend → act via
  `DesignService.run()`/`.resume()` → reflect, looping on resumable failures up
  to a bound.
- **Added** `OrchestratorAgent.execute_until_goal()`; the Phase-26 `execute()`,
  `plan()`, `reflect()` are unchanged.
- **The loop is now the default execution path** (API runner drives
  `execute_until_goal`). Safe because the agent is a thin delegator: a no-op run
  (no checkpoints, first act succeeds) runs one iteration and is
  **byte-identical to Phase 26** — asserted by a direct-vs-agent test.
- **Added** `max_resume_attempts` to `QAOpsSettings` (default 2). The agent
  decides whether another resume is worthwhile; per-stage provider/model retry
  and failover remain owned by `AdaptiveExecutor`, unchanged. `CheckpointStore`
  is unchanged and only read.
- **Terminal conditions**: completed, max resume attempts, clarification needed
  (unresolved/gap thresholds), or manual review needed (repeated stage failure).
  Each iteration records its observation + structured decision.
- **Additive API/UI**: run status gains an optional `loop_summary` (iterations,
  decisions, terminal reason, cumulative reflection); a new run-page panel shows
  it. No existing endpoint, field, or workflow changed; pre-loop runs have a null
  loop_summary. The underlying stage error, failed stage, and attempt history are
  preserved on terminal failure (secret-redacted as before).
- **Tests**: +19 backend (`tests/test_phase27_goal_driven_loop.py`) — observe,
  all decide branches, resume-loop recovery across iterations, stop conditions,
  clarification/manual-review, reflection accumulation, direct-vs-agent artifact
  identity, and the cannot-execute-stages / cannot-generate-artifacts /
  cannot-write-checkpoints invariants; +1 frontend (loop panel). Backend 780
  passed; frontend 62 passed.
- **ADR-042**. Version stays `0.18.0-dev`.

### Phase 26: The Orchestrator Agent (first agentic capability)

Introduces QAOps' first agent. It reasons about HOW the deterministic pipeline
executes — builds an execution plan, decides resume-vs-restart, records the
decisions, and produces a post-run reflection — while the pipeline stays the
sole author of every artifact (requirements, business rules, gaps, scenarios,
conditions, test cases, coverage). All changes additive (ADR-041).

- **Added** `qaops/agent/` package (extension point for future agents):
  `base.py` (Agent ABC), `models.py` (ExecutionPlan / PlanStep / Decision /
  Reflection), `planner.py`, `reflection.py`, `orchestrator.py`.
- **`OrchestratorAgent`** plans, delegates execution to the unchanged
  `DesignService.run()` / `.resume()`, and reflects. It has no method that emits
  a pipeline artifact.
- **Two-layer reasoning**: structure is deterministic (stage list from the entry
  point; reuse/resume from `CheckpointStore`; successes/failures/retries from the
  manifest and attempt history; clarification recommendation from the
  deterministic unresolved-conditions/gap signal). An optional LLM enriches the
  human-readable prose only, is forbidden from changing which stages run, and
  degrades to deterministic text on failure.
- **Determinism preserved**: with no intervention (no checkpoints → full run),
  agent-driven execution is byte-identical to Phase 25 — asserted by test.
- **Decision rules**: checkpoints → resume not restart; succeeded stage → reuse;
  repeatedly failing stage → stop (don't re-resume); ambiguity over threshold →
  recommend clarification. Each decision records reason + alternative + why
  rejected.
- **Additive API/UI**: run status gains `plan` and `reflection` fields (new
  schemas); the run page shows an execution-plan panel (steps + decisions) and a
  reflection panel (summary, recommendations, lessons). No existing endpoint,
  field, or workflow changed; pre-agent runs render as before. Plan/reflection
  generation is best-effort and never blocks or fails a run.
- **Tests**: +18 backend (`tests/test_phase26_orchestrator_agent.py`) — plan
  generation, skipped-stage planning, resume/restart decision, explanation
  enrichment + LLM-failure fallback, reflection (successes, clarification/gap
  recommendations, retry lessons, recovered stages), deterministic no-op
  identity, the "agent never generates artifacts" invariant, checkpoint reuse;
  +3 frontend (plan/reflection panels). Backend 758 passed; frontend 55 passed.
- **ADR-041**. Version stays `0.18.0-dev`.

### Phase 25: Execution checkpointing, partial artifacts & resume

Makes runs resilient to mid-pipeline failure: completed stages are checkpointed,
their artifacts are exported even when a later stage fails, and a failed run can
resume from the last checkpoint without re-running completed stages (ADR-040).
Pipeline stages, provider execution, and orchestration semantics are unchanged;
every change is additive.

- **Added** `CheckpointStore` (`qaops/execution/checkpoint.py`): atomic per-stage
  JSON checkpoints under the run workspace, a manifest of stage statuses, and
  deterministic rehydration into the exact Pydantic model. Corrupt/missing/
  unknown-type checkpoints raise `CheckpointError`.
- **Excluded** `source_text` from checkpoint payloads: because stage outputs are
  cumulative it was duplicated in every checkpoint (~7x); it is the raw input,
  already on disk, and read by no downstream stage. A placeholder is re-injected
  on rehydration. Delta checkpoints deferred as future work.
- **Added** an optional `checkpoint` sink and `start_index` to `AdaptiveExecutor`
  (default no-op / 0 — CLI and tests unchanged; failover untouched).
- **Added** partial export on failure: the service promotes the latest
  checkpoint to a partial `TestDesignResult` and writes the artifacts for
  completed dimensions only; a stage that raised never checkpoints, so no
  half-computed downstream artifact can appear.
- **Added** `DesignService.resume()` and `runner.resume_run()`: reuse completed
  stages, run only the remainder; fall back to a full run if no checkpoint.
- **Extended** run state additively: `RunStatus` gains
  `PARTIALLY_COMPLETED`/`RESUMABLE`/`CANCELLED` (original four unchanged); `Run`
  gains `stage_statuses`/`resumable`/`cancel_requested`/timings. New endpoints
  `POST /runs/{id}/resume` and `POST /runs/{id}/cancel`; status response surfaces
  per-stage statuses, `resumable`, and `completed_stages`.
- **UI**: partial-completion state, completed-stages list, per-artifact
  downloads, and a Resume button.
- **Scope**: in-process resume only; restart-resilient registry rebuild is
  future work. Cancellation is cooperative (honoured at stage boundaries).
- **Tests**: +16 backend (`tests/test_phase25_checkpoint_resume.py`) covering
  round-trip, source_text exclusion, corrupt/missing/unknown checkpoints,
  fail-at-stage → partial export, resume without re-running completed stages,
  no-checkpoint fallback, second-failure-stays-resumable, last-stage resume; +1
  frontend (partial/resume UI). Backend 740 passed; frontend 53 passed.
- **ADR-040**. Version stays `0.18.0-dev`.

### Phase 24: Multi-format document ingestion (DOCX)

Adds Microsoft Word `.docx` as a first-class requirement-document input
alongside PDF, text, and Markdown. Input-layer only: the pipeline
(RequirementAnalyzer through Coverage) and provider/execution architecture are
untouched (ADR-039).

- **Implemented** `DocxLoader` (was a registered placeholder) using python-docx:
  walks the document's paragraph/table block stream in order and renders
  Markdown-shaped normalized text — title/headings as ATX headings, numbered
  lists as `1. `, bullets as `- `, tables flattened to pipe-delimited rows with
  a header separator. Output passes the shared `normalize_text` contract, so
  downstream stages receive the same normalized-text model as any other format
  and never learn the source.
- **Added** the `[docx]` optional extra (`python-docx>=1.1`) and its install
  hint; a missing install raises a friendly message, as with `[pdf]`.
- **Rejection**: `.doc`, `.ppt`, `.xls`, `.zip`, and images remain unregistered
  and produce a friendly unsupported-format error; a non-package file reaching
  the loader raises a clear "not a valid Word .docx" error. Text/Markdown remain
  supported (not rejected) to preserve backward compatibility.
- **Empty / malformed** documents raise `DocumentLoadError` with clear causes,
  mirroring the PDF loader.
- **UI**: already labelled "Upload a requirement document" and already accepted
  `.docx` with meaningful validation — no change required.
- **No pipeline, coverage, API, or provider change.** The architecture Phase 24
  specified (loader protocol + registry factory + shared normalized model)
  already existed from ADR-018; this phase filled in the DOCX loader.
- **Tests**: +17 (`tests/test_phase24_docx_ingestion.py`) — headings,
  paragraphs, numbered/bullet lists, tables, document order, mixed formatting,
  empty, malformed, registry dispatch, install hint, and a DOCX-vs-Markdown
  normalization-equivalence regression: the same content as DOCX and as Markdown
  produces byte-identical normalized text, so ingestion guarantees identical
  pipeline input regardless of source format (downstream artifact equivalence
  then depends on provider/model determinism, not on ingestion). Existing
  ingestion stub tests updated. Backend 724 passed; frontend 52 passed.
- **ADR-039**. Version stays `0.18.0-dev`.

### Phase 23: Technique-driven test-case expansion

Breaks the condition->case 1:1 bottleneck by operationalising each condition's
QA technique into concrete case variants, deterministically (ADR-038).

- **Added** `ExpansionPlanner` (`qaops/pipelines/test_design/expansion.py`): a
  pure, deterministic planner that turns each condition's `category` +
  `parameters` into bounded `ExpansionSlot`s — the variants the technique
  requires, from documented evidence only. Boundary -> below/at/above;
  equivalence -> one per partition; state_transition -> one per transition;
  data/role variation -> one per documented value; single-variant techniques and
  unresolved conditions -> one slot. It never invents a number, partition, or
  state; a single-dimension condition stays 1:1.
- **Changed** `TestCaseGenerator` to plan first, then have the model author
  exactly one case per slot (echoing `slot_id`). The count is now a
  deterministic function of documented dimensions, not model whim.
- **Added** optional `slot_id` and `technique` to `TestCase` for per-case
  technique-level traceability (both defaulted; artifacts, API, exports, and
  frontend unaffected).
- **Rewrote** the test-case prompt to be plan-driven (one case per slot, use the
  slot's `parameter_delta` as concrete test data).
- **Preserved**: canonical-signature dedup (collapses identical slot output,
  keeps variant data), bounds/`expansion_truncated`, evidence validation,
  condition-coverage semantics, provider/execution architecture, and API/export
  compatibility. Frontend types extended additively; no behavioural change.
- **Tests**: +21 (`tests/test_phase23_expansion_techniques.py`) — planner unit
  tests for every supported technique plus end-to-end proofs (boundary->3,
  equivalence->N, unresolved->1 provisional, positive 1:1, cross-slot dedup,
  coverage/traceability). Backend 709 passed; frontend 52 passed.
- **ADR-038**. Version stays `0.18.0-dev`.

### Phase 22: Condition expansion & ambiguity integrity

Fixes a behavioural weakness found in production Phase 21 runs, where each
scenario yielded exactly one condition and one case, and known gaps coexisted
with 100% condition coverage. No new stage, no model/API/frontend redesign
(ADR-037).

- **Added** a deterministic gap -> unresolved-condition linkage: a condition is
  forced `UNRESOLVED` (and linked to the gap) when a requirement-analysis gap
  both targets a requirement the condition tests and matches the condition's
  subject. Informational or non-matching gaps are left alone. A gap that blocks
  testable behaviour can no longer coexist with 100% condition coverage.
- **Added** the gap report to the `TestConditionAnalyzer` prompt inputs
  (`gaps_json`), and rewrote the prompt for technique-driven derivation (list
  each scenario's documented dimensions; worked decision-table example; forbids
  fabricated expected results including "confirm with product owner").
- **Added** a deterministic guard rejecting a `negative` condition whose
  description states the criteria ARE met (the COND-006 contradiction class).
- **Changed** the scenario prompt minimally: scenarios are behaviour-level, not
  one-per-condition, so the analyzer has something to decompose. Broader
  scenario-granularity tuning is deferred to Phase 22.1.
- **Added** deterministic expansion diagnostics (counts only; no prompts,
  secrets, or document content) via the existing logger.
- **Preserved**: evidence validation, canonical-signature dedup with boundary
  survival, expansion bounds and `expansion_truncated`, condition-coverage
  arithmetic, all Phase 21 models/IDs/entry-points/API/exports/frontend, and the
  provider/execution architecture.
- **Tests**: +17 (`tests/test_phase22_condition_integrity.py`) with a BOGO/cart
  fixture proving multi-condition derivation, gap->unresolved, gap reuse,
  coverage drop below 100%, dedup/bounds, contradiction rejection, and
  legitimate 1:1. Backend 688 passed. No frontend change.
- **ADR-037**. Version stays `0.18.0-dev`.

### Phase 21: Exhaustive, evidence-bound test design (test conditions)

Replaces the de-facto one-case-per-scenario behaviour with technique-driven,
evidence-bound expansion, without changing the provider/execution architecture
(ADR-036).

- **Added** a `TestCondition` domain concept between `Scenario` and `TestCase`,
  making the design chain REQ -> BR -> SC -> COND -> TC. New enums
  `ConditionCategory`, `SourceBasis`, `ConditionStatus`; `COND-*` IDs assigned by
  code.
- **Added** the `TestConditionAnalyzer` pipeline stage (scenarios ->
  conditions -> cases -> coverage) with deterministic evidence validation: a
  derived boundary/equivalence/combination/state condition must cite the rule or
  requirement carrying its basis, or it is rejected. No invented behaviour.
- **Changed** `TestCaseGenerator` to be condition-driven: one or more cases per
  condition, only when data/boundary/state genuinely differ. No fixed
  scenario:case or condition:case ratio.
- **Added** deterministic canonical-signature de-duplication for conditions and
  cases that preserves legitimate boundary variants (quantity 2 vs 3) while
  collapsing restatements; duplicate cases are dropped, not raised.
- **Added** ambiguity handling: an unresolved condition is preserved, linked to
  a synthesized gap (deduplicated against existing gaps), and its case is marked
  `provisional`; unresolved conditions never count as covered.
- **Added** expansion bounds (`max_conditions_per_scenario`,
  `max_cases_per_condition`, `max_total_test_cases`); hitting a bound sets
  `expansion_truncated` and a visible note rather than silently dropping
  candidates.
- **Changed** coverage to multi-dimensional (requirement, business-rule,
  scenario, condition) with `condition_coverage_pct`; the headline
  `coverage_percent` is retained for compatibility and equals requirement
  coverage. Coverage is labelled as non-exhaustive.
- **Added** API fields (`test_conditions`, per-dimension coverage percentages,
  `unresolved_conditions`, `expansion_truncated`) and a **Test Conditions** tab
  plus multi-dimension coverage view in the frontend, with provisional and
  truncation indicators.
- **Added** `condition_id`/`provisional` columns to CSV export; JSON already
  carries conditions.
- **Compatibility**: `TestCase.condition_id`/`provisional` are optional
  defaults; the SCENARIOS entry point still works (conditions derived from the
  supplied scenarios); Phase 19/20 provider/execution architecture unchanged.
- **Tests**: +15 (`tests/test_phase21_expansion.py`) plus condition-driven
  updates across the suite. Backend 671 passed; frontend 52 passed.
- **ADR-036**. Version stays `0.18.0-dev`.

### Phase 20: Provider reliability, model eligibility & failure observability

Production-reliability fixes from a real failed smoke test, without redesigning
the AdaptiveExecutor or adding providers (ADR-035).

- **Added** capability-first model eligibility: `ModelInfo.text_capable`;
  OpenRouter discovery reads `architecture` modalities; the selector filters
  non-text models (e.g. music/image generators like the incident's
  `google/lyria-*`) **before** ranking, so a large context window cannot rescue
  an unsuitable model.
- **Added** Gemini model discovery (`discover_gemini_models`) via the
  google-genai SDK `models.list()` filtered on `generateContent`, cached by the
  existing `ModelRegistry`, with a static fallback updated to stable `*-latest`
  aliases (`gemini-flash-latest`/`-lite`/`-pro`) instead of the retired pinned
  `gemini-2.5-flash`. Default `gemini_model` is now `gemini-flash-latest`.
- **Added** structured-field failure classification:
  `LLMProviderError.status_code`/`error_code` (sanitized),
  `classify_failure_fields`, `recovery_for_exception`. Resolves the
  `rate_limit → unknown` sequence — an opaque 429 is now reliably a rate limit
  via HTTP status. A provider-wide error code (`insufficient_quota`, billing
  hard limit) disables the provider; a plain transient 429 stays bounded retry.
- **Added** failure-chain observability: ordered sanitized `attempt_history`
  (stage/provider/model/failure_kind/status/code) on `ExecutionReport`,
  `StageError`, and the API run status response, so a failed run shows the whole
  failover story rather than only the last error. No secrets are recorded.
- **Unchanged**: execution bounds (5/12/20), ANY/FREE_FIRST/FREE_ONLY semantics,
  the FREE_ONLY no-paid guarantee, and pipeline behaviour.
- **Tests**: +19 (`tests/test_provider_reliability.py`) covering unsuitable-model
  rejection, Gemini 404, Groq rate-limit scope, bounded UNKNOWN recovery,
  end-to-end exhaustion with attempt history, and credential sanitization.
  Backend 654 passed / 1 skipped.
- **ADR-035**. Version stays `0.18.0-dev`.

### Phase 19: Free-capacity expansion (Groq, free-execution strategy, OpenRouter quota)

Adds independent free inference capacity and a way to run only on free capacity,
without changing the AdaptiveExecutor architecture (ADR-034).

- **Added** `GroqClient` (`qaops/llm/groq_client.py`) behind the existing
  `LLMClient` abstraction, reusing the OpenAI-compatible client (no new SDK).
  Verified free model IDs `llama-3.3-70b-versatile`, `openai/gpt-oss-120b`,
  `llama-3.1-8b-instant`. Missing `GROQ_API_KEY` makes Groq unavailable.
- **Added** `ExecutionStrategy` (`any` / `free_first` / `free_only`) via the new
  `QAOPS_EXECUTION_STRATEGY` setting. `free_only` never invokes paid providers
  (Anthropic is dropped); `free_first` exhausts free-eligible candidates before
  paid. Eligibility is per-candidate (`ModelInfo.free`), so Gemini's free
  `2.5-flash` and OpenRouter `:free` models are usable while their paid models
  are not. Default `any` preserves existing behaviour exactly.
- **Added** `FailureKind.PROVIDER_RATE_LIMIT`: an account-wide OpenRouter
  exhaustion (`free-models-per-day`) now disables OpenRouter for the rest of the
  run instead of walking more free models. Model-specific/transient 429s keep
  bounded retry and do not disable the provider.
- **Changed** the registry: Groq `ProviderInfo` added; `gemini-2.5-flash` marked
  free and `gemini-2.5-pro` paid (per-model eligibility).
- **Unchanged**: recovery bounds (5 / 12 / 20), telemetry (existing execution
  events already expose provider/model/calls/recovery/failure/switches/disable
  reason), and all prior default behaviour.
- **Tests**: +18 (5 Groq client, 9 free-execution strategy, 4 OpenRouter
  quota), all without live LLM calls or real credentials. Backend 635 passed /
  1 skipped.
- **ADR-034**. Version stays `0.18.0-dev`; nothing tagged or released.

### Phase 18: Public production deployment (Render, single service)

QAOps can be deployed as one Render Web Service with one public URL: FastAPI
serves both the API and the built React frontend from the same origin (ADR-033).

- **Added** FastAPI static/SPA serving (`_mount_frontend` in `qaops/api/app.py`):
  `/assets` served from the Vite build; `index.html` served at `/` and for
  non-API paths so React Router handles `/`, `/design`, `/runs/{id}` on direct
  navigation and refresh. API routes and `/health` always take precedence; an
  unknown `/api/*` returns an API 404 (JSON), never `index.html`. A missing
  build degrades cleanly (503 notice) while the API stays functional.
- **Added** `APIConfig.static_dir` (env override `QAOPS_STATIC_DIR`), defaulting
  to `frontend/dist`.
- **Changed** the frontend API base URL to default to same-origin (`""`); added
  `frontend/.env.development` (localhost:8000 for dev) and
  `frontend/.env.production` (empty). No backend host is baked into the
  production bundle. Local Vite + FastAPI workflow is unchanged.
- **Added** `render.yaml` (build installs Python + provider extras + frontend,
  runs uvicorn on `$PORT`, `/health` health check, Python 3.12 / Node 22
  pinned, provider-key variable names declared with `sync: false`).
- **Changed** `.gitignore` to exclude a repo-root `qaops.yaml` so production
  cannot accidentally depend on a developer's local config.
- **Changed** the run failure view to show a concise summary with the raw
  provider error moved into an optional collapsible details block
  (presentation only; no pipeline/recovery change).
- **Tests**: 16 backend deployment tests (`tests/test_deployment.py`) and 2
  frontend failure-UX tests, all without live LLM calls or real credentials.
  Backend 617 passed / 1 skipped; frontend 50 passed.
- **ADR-033**. Version stays `0.18.0-dev`; nothing tagged, released, or
  deployed.

### Phase 17: Web UI MVP

A React + TypeScript + Vite single-page app in `frontend/` over the existing
FastAPI backend (ADR-032). Users can upload a requirement document, watch run
progress, review generated QA results, and download artifacts without the CLI
or Swagger.

- **Added** `frontend/`: a typed API client (`src/api/client.ts`) that is the
  sole `fetch` boundary, with `ApiError`/`NetworkError` and request
  cancellation; TypeScript types (`src/api/types.ts`) derived from the real
  OpenAPI schema and JSON artifact — including the true `blocker|major|minor`
  gap severities, not invented values.
- **Added** an upload page (drag-drop + file picker, extension/empty
  validation against the backend's accepted formats, double-submit guard), a
  run page (2s polling that stops on terminal status, a six-stage stepper,
  structured progress showing provider/model/`provider_call_number`/recovery
  count, and a concise failure view), and a tabbed results dashboard
  (requirements, business rules, scenarios, test cases, gaps, coverage) sourced
  from the run's JSON artifact.
- **Backend unchanged**: no endpoints added, no pipeline changes; CORS already
  allowed the Vite dev origins from Phase 16.
- **Tests**: 48 frontend tests (Vitest + Testing Library) across the API
  client, upload page, polling hook, run page, results views, and app shell —
  all with mocked API responses, zero LLM cost. Backend remains at 592 tests.
- **Gates**: TypeScript typecheck, ESLint (0 warnings), and production build
  all pass.
- **ADR-032**. Version stays `0.18.0-dev`; no bump or tag for this phase.

### Phase 16.2 acceptance fix

Phase 16.2 acceptance fix: nested structured-output retries are now counted and
bounded. The real PDF acceptance run showed that schema-repair could make
several real provider calls inside one executor attempt, invisible to progress
and budget. Fixed so one actual provider call equals one counted request
(ADR-030 addendum).

### Added

- **`RequestObserver` seam** (`qaops/llm/request_budget.py`): the executor binds
  an observer around each stage run; `generate_structured` announces every real
  provider call to it. Each call is counted, made visible as REQUEST_STARTED /
  REQUEST_COMPLETED events, and can be vetoed when the budget is spent.
- **`max_provider_calls_per_stage` setting** (default 20): a hard ceiling on
  actual provider calls per stage, including structured-output repair calls.
  Distinct from `max_stage_recovery_attempts` (which counts switches), so
  recovery semantics are unchanged.
- **`FailureKind.EMPTY_OUTPUT`** and **`LLMEmptyResponseError`**: an empty
  provider response is now a distinct, accurately-diagnosed failure that moves
  to the next model without a repair re-roll.
- **`provider_call_number`** on execution events and API progress: the honest
  running total of real provider calls for the stage.
- **REQUEST_COMPLETED / REQUEST_FAILED** events, one pair per real call.
- **Tests:** 24 new (592 total), including the exact live gap_analyzer fixture
  (cohere empty/length responses), an instrumented proof that actual fake
  provider calls equal the accounting, and coverage of empty/invalid/truncation
  handling and the provider-call budget.

### Changed

- **`invalid_output` now recovers with NEXT_MODEL, not RETRY_SAME.** A model
  reaching the executor with invalid output has already exhausted its in-request
  repair attempts; another full nested cycle on the same model is waste.
- **Empty responses stop the repair loop immediately** - re-prompting a model
  that returned nothing cannot help.
- **Truncation diagnostic corrected**: `stop_reason=length` with zero content is
  no longer diagnosed as token truncation and no longer recommends raising
  `max_output_tokens`. That advice is emitted only for genuinely cut-off,
  non-empty output.
- The `LLMClient` protocol now declares `model` (all concrete clients already
  had it), used by the request-observer path.
- Version stays `0.18.0-dev`; no bump for this acceptance fix.

### Guarantees preserved

All Phase 16.2 guarantees hold: `request_timeout_seconds` default 60 and the
`QAOPS_REQUEST_TIMEOUT_SECONDS` override, disabled SDK retries, Gemini timeout
config, timeout normalization, same-model timeout retry policy, the 5-model and
12-recovery bounds, provider failover, checkpointed completed stages, single-
and multi-provider progress, and CLI compatibility.

---

## [0.18.0-dev] - Phase 16.2 (superseded above)

Phase 16.2: request timeout guard and accurate runtime progress. A single
provider request can no longer hold a stage indefinitely, and progress is
observable and unambiguous for every run (ADR-030). Builds on Phase 16.1.

### Added

- **`request_timeout_seconds` setting** (default 60, validated, backward-
  compatible). Passed by the factory to every provider client and applied at the
  SDK/HTTP boundary. Bounds a single generation request — not a stage, the
  pipeline, or all retries combined.
- **QAOps-owned retries.** SDK internal retries are disabled (`max_retries=0` on
  the Anthropic and OpenAI/OpenRouter SDKs), so one QAOps attempt is exactly one
  network request with one deadline. This was the real cause of the observed
  12-minute stall: the OpenAI SDK's default 2 retries silently multiplied one
  attempt into three ~120s requests.
- **Timeout normalization** (`qaops/llm/timeouts.py`): SDK timeout exceptions
  are detected at the provider boundary and rewritten so the existing policy
  classifies them as `timeout`. Conservative — a plain connection error is not
  treated as a timeout.
- **Request lifecycle events**: `request_started` (before each call, making an
  in-flight request visible), `request_timed_out`, `request_retry`.
- **Unambiguous progress counters**: `model_attempt_number` (which distinct
  model for the stage) and `request_attempt` (which network request for the
  current model). The prior `models_attempted` is retained for compatibility.
- **Single-provider progress**: all execution now routes through the adaptive
  executor, so single-provider runs emit the same events as failover runs
  (Phase 16.1 gap closed). Providers without model metadata get a synthetic
  candidate so they stay executable.
- **Tests:** 45 new (567 total), including the live-failure regression (four
  OpenRouter models fail, the fifth times out, bounded recovery to Gemini),
  timeout propagation to all three providers, disabled SDK retries, timeout
  normalization without misclassifying network errors, the request lifecycle
  events and counter semantics, API in-flight progress (captured mid-request on
  a real thread), and no-secret checks.
- **ADR-030.**

### Changed

- Default per-request timeout lowered from a hardcoded 120s to a configurable
  60s.
- CLI adaptive output and API progress now reflect request-level state.
- Version stays `0.18.0-dev`; no bump for this phase (unreleased development
  work).

### Interaction of the execution bounds

`request_timeout_seconds` caps one request's duration; `max_attempts_per_model`
caps same-model retries; `max_models_per_provider_per_stage` (5) caps distinct
models per provider; `max_stage_recovery_attempts` (12) caps total switches.
Same-model retries do not consume the recovery budget.

---

## [0.18.0-dev] - Phase 16.1 (superseded above)

Phase 16.1: adaptive execution hardening and API progress. Bounds recovery so
live model discovery cannot cause hundreds of attempts, and exposes structured
run progress over HTTP (ADR-029). Builds on Phase 16.

### Added

- **Bounded candidate selection** (`qaops/execution/selector.py`): filters
  incompatible models, ranks the rest deterministically (configured model first,
  then structured-output support, priority, and context/output headroom), and
  returns a small pool. The executor no longer iterates the full discovered
  catalogue.
- **Two execution bounds** in settings, both configurable and validated, both
  backward-compatible with existing `qaops.yaml` files:
  `max_models_per_provider_per_stage` (default 5, counts distinct models, not
  same-model retries) and `max_stage_recovery_attempts` (default 12, total
  recovery actions per stage).
- **Structured execution events** (`qaops/execution/events.py`): the executor
  emits typed events at stage and failure boundaries. The CLI renders them as
  text; the API converts them to run progress. The executor stays HTTP-unaware.
- **API run progress**: `GET /api/v1/runs/{id}` now returns a `progress` object
  (current stage, position, provider, model, models attempted, recovery
  attempts, safe message), preserved after completion. Failed runs expose
  `failed_stage` and `recovery_attempts`. No secrets, no raw provider payloads.
- **Tests:** 19 new (522 total), including a stress test proving a 300-model
  credit-exhausted catalogue yields at most 5 attempts per provider before
  failover, plus selector ranking/filtering, both budgets, no-infinite-loop,
  completed-stage preservation, and API progress without secrets.
- **ADR-029.**

### Changed

- `insufficient_credit` remains bound-only: the "can only afford N" figure is
  not used to infer account vs model exhaustion (it is not a reliable signal);
  the per-provider model cap is the protection.
- Version `0.17.0` → `0.18.0-dev`. See the version note below.
- CLI adaptive output now shows the model per stage (e.g.
  `test_case_generator: anthropic/claude-sonnet-4-6 ok`).

### Version note

The working tree read `0.17.0` (no `-alpha`) because the Phase 15 model-discovery
work bumped `pyproject.toml` to `0.17.0` as a working version; the health
endpoint reads that via `importlib.metadata`, which is why a never-released tree
reported `0.17.0`. No git tag was ever created. The current unreleased work
(Phase 16 + 16.1) now carries `0.18.0-dev` to distinguish it from a real release.
`qaops/__init__.py` was also corrected from a stale `0.1.0`.

## [0.17.0-alpha - superseded by 0.18.0-dev] - Phase 16: FastAPI backend

Phase 16: FastAPI backend foundation. QAOps is now reachable over HTTP as well
as the CLI, both running the same orchestration (ADR-028).

### Added

- **`DesignService`** — the design orchestration (classify, preflight, parse,
  build, adaptive-execute, write reports) extracted from the CLI so the CLI and
  API share it. Progress is emitted through a caller-supplied callback. ADR-023
  output-collision safety and friendly filesystem errors moved with it.
- **FastAPI application** (`qaops.api.app:app`, optional `[api]` extra) with:
  `GET /health`, `GET /api/v1/models`, `POST /api/v1/design` (multipart upload,
  auto-detected workflow, returns 202), `GET /api/v1/runs/{id}`,
  `GET /api/v1/runs/{id}/artifacts`, and
  `GET /api/v1/runs/{id}/artifacts/{name}`.
- **Asynchronous runs**: an in-memory, thread-safe `RunStore` behind a small
  interface, with a per-run workspace (`input/` + `output/`) so runs cannot
  overwrite each other. Background execution transitions a run through
  queued → running → completed | failed.
- **Safety**: secret redaction on run errors, no tracebacks in responses,
  path-traversal-proof artifact download, explicit configurable CORS (never
  `*` with credentials), sanitized upload filenames.
- **OpenAPI docs** at `/docs`.
- **Tests:** 30 new (503 total) — startup, health, models and secret
  non-exposure, upload lifecycle (completed and failed), unknown run, artifact
  listing/download, path-traversal rejection (three encodings), workspace
  isolation, adaptive-path reuse, and the DesignService in isolation.
- **ADR-028.**

### Changed

- The CLI's `_run_design` now delegates to `DesignService`; behaviour is
  unchanged. The now-duplicated CLI helpers (`_write_reports`,
  `_check_output_collisions`, `_friendly_write_error`, `_fallback_providers`)
  were removed.
- `/health` reports the version from package metadata, not the stale
  `qaops/__init__.py` constant.

### Known limitations

- Run state is in memory: a process restart loses all run status. Workspaces on
  disk survive; the registry indexing them does not. Acceptable for local,
  single-process use; a persistent store is the seam for later.

## [0.17.0-alpha] - 2026-07-25

Phase 15 revision: model-then-provider failover with runtime model discovery
(ADR-027). Building on the provider failover from 0.16.0.

### Added

- **Model-level failover.** When a model fails, the executor tries the next
  compatible model on the *same* provider before switching providers. A
  provider is abandoned only once every compatible model has failed, so a
  working credential is not discarded over one model's exhausted credit.
- **Runtime model discovery.** `discover_openrouter_models` parses OpenRouter's
  public models endpoint; `discover_ollama_models` reads a local daemon's tags.
  Both degrade to a curated static table on any failure. `ModelRegistry`
  discovers, caches per run, refreshes on request, and exposes capability
  metadata (context and output limits, structured output, locality).
- **Model-aware retry policy.** Credit exhaustion tries the next model; model
  unavailable drops it; context overflow asks for a larger-context model;
  authentication disables the provider; rate limits back off; timeouts and
  invalid output retry the same model.
- **`qaops models` command** lists what each available provider discovers, with
  `--refresh` and `--static`, so discovery is verifiable on its own.
- **Tests:** 55 new/rewritten — model and provider failover, capability
  filtering, discovery parsing and graceful degradation, caching and refresh,
  the models command, and a regression for a single-provider retry loop.
- **ADR-027.**

### Fixed

- **A single-provider retry loop.** Model-first recovery could cycle forever
  when one provider's every model gave the same retryable failure (e.g.
  timeout): retries exhausted, a sibling was tried, attempts reset, repeat. The
  executor now tracks models already tried for the current stage and excludes
  them, so exhaustion raises a clear error instead of hanging.

### Changed

- `StageCheckpoint` and `ExecutionReport` now record the model per stage, and
  the report exposes `model_switches` and `models_used`.
- Package version 0.16.0 → 0.17.0. No pipeline stage, prompt, exporter,
  chunking, or PipelineBuilder change.

### Note

Model discovery is tested against mocked HTTP responses only; the build
environment has no network access to provider APIs. If a live response shape
differs, discovery degrades to the static table and execution still works. The
`qaops models` command exists to verify discovery against a real key.

## [0.16.0-alpha] - 2026-07-24

Phase 15: adaptive execution. A run now survives a provider failing mid-
pipeline, without recomputing completed stages (ADR-026).

### Added

- **`AdaptiveExecutor`** - runs stages one at a time, checkpointing each
  success. On failure it classifies the error, applies policy, and where a
  switch is warranted rebuilds the *remaining* stages against the next healthy
  provider and resumes from the failed stage. Completed stages are never
  recomputed, and no stage learns which provider serves it.
- **Failure classification and retry policy**: exhausted credit and rejected
  credentials disable the provider; rate limits retry with backoff; timeouts
  and schema-validation failures retry the same provider; context overflow
  switches without disabling.
- **Provider registry** describing each provider's key variables, locality,
  priority, and structured-output support. Adding a provider means adding a
  row. Preflight now reads key metadata from here rather than a second copy.
- **Automatic failover chain**: the configured provider leads, and every other
  provider with credentials present follows in priority order, so failover
  needs no configuration while an explicit choice still wins.
- **Per-stage progress output** showing which provider ran each stage and why
  any switch occurred.
- **Tests:** 38 new (456 total) - classification for every failure kind, each
  policy branch, mid-pipeline switching, checkpoint preservation, health
  tracking and provider skipping, retry and backoff behaviour, exhaustion, and
  registry availability rules.
- **ADR-026:** adaptive execution.

### Changed

- `Pipeline` exposes a read-only `stages` property so an executor can run
  stages individually. Its behaviour is unchanged.
- Package version 0.15.0 → 0.16.0. No pipeline stage, prompt, exporter,
  chunking, or PipelineBuilder change.

### Note

Checkpoints live in memory for the duration of a run, which covers the failure
this phase addresses. Persisting them across process restarts raises staleness
questions and is deliberately out of scope.

## [0.15.0-alpha] - 2026-07-24

Phase 14: automatic workflow selection. `--from` is now optional - QAOps
detects the entry point from the input and reports what it found (ADR-025).

### Added

- **Deterministic input classification.** Extensions resolve unambiguous cases;
  CSV and JSON are classified by headers and keys, markdown and text by whether
  they contain a scenario table or an explicitly marked scenario list. No LLM
  call is made to classify.
- **Pre-flight validation** before any pipeline work: file exists and is a
  file, the optional dependency for its format is installed, and the provider's
  API key is present. Failures are reported as one actionable message rather
  than surfacing several stages in.
- **Detection feedback**: the CLI prints what it detected and why, e.g.
  `Detected: scenario spreadsheet (.xlsx is a spreadsheet of scenarios)`.
- **Tests:** 34 new (418 total) - classification across every supported format,
  pre-flight checks, automatic routing in the CLI, explicit `--from` override,
  PipelineBuilder delegation, and regressions for prose-with-lists.
- **ADR-025:** deterministic workflow detection.

### Fixed

- **A prose PRD with numbered acceptance criteria is no longer mistaken for a
  scenario list.** The first classifier treated any bulleted or numbered list
  as scenarios, which routed `examples/login.md` down the scenario path where
  it failed. List items now count only when marked with a requirement
  reference or category tag, and a majority must be marked.
- **A corrupt or non-spreadsheet `.xlsx` now fails gracefully.** openpyxl
  raises `BadZipFile`, which is not an `OSError` and previously escaped as an
  unhandled exception.
- **An empty description cell no longer rejects a requirements row**; it falls
  back to the title, which hand-maintained sheets rely on.

### Changed

- `--from` is optional. When omitted the entry point is detected; when supplied
  it wins and detection is skipped.
- Pre-flight runs before the provider client is constructed, so a missing API
  key is caught before any work. Existing CLI tests now set a key.
- Package version 0.14.0 → 0.15.0. No pipeline stage, prompt, exporter, or
  PipelineBuilder change.

## [0.14.0-alpha] - 2026-07-23

Phase 13: human-authored scenario documents. QA teams can feed their existing
scenario artifacts straight into the pipeline (ADR-024).

### Added

- **XLSX scenario input** (`.xlsx`, `.xlsm`): first worksheet, first non-empty
  row as the header, blank rows skipped, requirement references split on any
  common separator.
- **Markdown table scenario input**: the first pipe table containing a title
  column.
- **Markdown and TXT list input**: bulleted or numbered items, with `REQ-001`
  style tokens read as requirement references and a parenthesised known
  category lifted out of the title.
- **Header aliasing** across all formats: names match case-insensitively and
  ignore spaces and underscores, so `Scenario Name`, `Type`, and
  `Requirement IDs` work as written in real spreadsheets.
- **Tests:** 21 new (384 total) - XLSX rows and edge cases, markdown tables,
  bulleted and numbered lists, header aliases, deterministic re-reads, clear
  failure on unstructured prose and unsupported extensions, and CLI runs from
  both XLSX and markdown.
- **ADR-024:** structured readers; prose stays with the analyzer.

### Changed

- `parse_scenarios` now accepts `.json`, `.csv`, `.xlsx`, `.xlsm`, `.md`,
  `.markdown`, and `.txt`. No pipeline stage, prompt, exporter, or
  PipelineBuilder change.
- Package version 0.13.1 → 0.14.0.

### Note

Prose requirement documents (PDF, DOCX, free-form markdown) are handled by the
existing `document` entry point, where the requirement analyzer extracts
requirements with a model. Structured readers stay deterministic and reject
prose with guidance rather than guessing.

## [0.13.1-alpha] - 2026-07-23

Phase 12.1: workflow safety and CLI hardening. No pipeline, prompt, parser,
exporter, or chunking change (ADR-023).

### Fixed

- **Reports can no longer overwrite their own input.** Running from a file
  inside the output directory - e.g. `design output/Requirements.csv --from
  requirements -o output` - would write a fresh `Requirements.csv` over the
  source. The CLI now computes every path it is about to write (single-file
  exports plus the csv-bundle filenames), compares against the resolved input,
  and aborts before writing anything, suggesting a different `--output-dir`.
  A rejected run leaves the directory untouched, with no partial bundle.
- **Filesystem failures no longer produce tracebacks.** `OSError` during
  export becomes a friendly message naming the file;`PermissionError`
  specifically suggests the file may be open in another application, which on
  Windows is usually Excel holding a CSV.

### Added

- **Provider error diagnosis.** Insufficient credit, rate limiting,
  authentication failure, context-length overflow, and unavailable-model
  errors are recognised from the provider's own text and rendered as a reason
  plus concrete next steps, with the original message always preserved.
  Provider failures wrapped inside a `StageError` are diagnosed too, so raw
  HTTP bodies no longer leak through stage messages. Unrecognised errors fall
  back to the previous raw-text behavior.
- **`CsvBundleExporter.BUNDLE_FILENAMES`** - the exact files the bundle
  writes, exposed so callers reason about them without duplicating the list.
- **Tests:** 20 new (363 total) - collision detection for both bundle and
  single-file exports, input bytes unchanged after an aborted run, no partial
  bundle written, permitted cases (different output directory, non-clashing
  name), provider-error classification including the wrapped-in-stage path,
  and filesystem error translation.
- **ADR-023:** fail safely.

### Changed

- Package version 0.13.0 → 0.13.1.

## [0.13.0-alpha] - 2026-07-23

Phase 12: multi-entry pipeline. QAOps is no longer a PRD processor - runs can
start from requirements or scenarios, composing the same stages (ADR-022).

### Added

- **Three entry points** via `--from`: `document` (default, unchanged),
  `requirements` (skips analysis), and `scenarios` (test-case generation and
  coverage only - a single LLM call versus five).
- **`PipelineBuilder`** (`build_pipeline_for`) returns the minimal stage
  sequence for a route by slicing the existing stage list. No stage is
  modified, subclassed, or duplicated, and no stage learns which route ran.
- **Input parsers** for JSON and CSV producing canonical domain models - the
  mirror image of exporters, with no generation logic and no LLM calls. The
  CSV columns match the csv-bundle export, so an exported bundle can be edited
  and fed back in. IDs are always reassigned by code (ADR-001).
- **Scenario entry synthesizes requirements** when the input does not supply
  them, because `TestCaseGenerator` validates requirement references
  (ADR-014). A bare scenario CSV therefore works.
- **Tests:** 27 new (343 total) - parser formats and validation failures, ID
  reassignment, builder stage selection per entry point, all three routes end
  to end, exporters from a non-document entry, and CLI routing including a
  friendly error for an unknown entry point.
- **ADR-022:** multi-entry pipeline composition.

### Changed

- Package version 0.12.0 → 0.13.0. No pipeline stage, prompt, domain model, or
  exporter changed; the existing suite passes untouched.

## [0.12.0-alpha] - 2026-07-23

Phase 11.1: adaptive chunking. Chunk sizing is now decided automatically from
provider and model capacity, so users rarely configure it (ADR-021).

### Added

- **`ChunkStrategy`** - decides whether to chunk, at what size, and with what
  overlap. `ChunkPlanner` no longer owns sizing policy and remains a pure
  deterministic text splitter.
- **Provider capability registry** (`capabilities.py`) - output capacity keyed
  by provider and model, with model overrides, provider defaults, and a
  conservative global fallback. Adding a provider means adding a row; the
  `LLMClient` protocol is untouched.
- **Automatic bypass** - a document within estimated capacity skips chunking
  entirely and the analyzer runs exactly as it would with no chunking.
- **Settings:** `chunking_strategy` ('adaptive' default, or 'fixed') and
  `chunk_safety_margin` (default 0.8). `chunk_size`/`chunk_overlap` are now
  used only under the fixed strategy.
- **Tests:** 28 new (316 total) - capability resolution and fallbacks, bypass
  for small documents, chunking for large ones, weak-model behavior,
  provider-specific capacity, safety-margin effects, determinism, fixed
  override, and analyzer integration.
- **ADR-021:** adaptive chunk sizing.

### Changed

- Sizing is derived from *output* capacity rather than input context, matching
  the observed failure mode (`stop_reason=length` during generation).
- Package version 0.11.0 → 0.12.0.

### Known limitation

`merge_requirements` still deduplicates on exact normalized title. Adaptive
sizing avoids chunking documents that fit, but a genuinely large document that
must be chunked can still yield near-duplicate requirements whose differing
titles survive the merge and produce duplicate scenarios. Chunking remains
unproven on documents that actually require it; a smarter merge is next.

## [0.11.0-alpha] - 2026-07-23

Phase 11: large-document chunking. Replaces evaluation mode as the answer to
document size. Chunking is an internal capability of requirement analysis and
is invisible to every downstream stage (ADR-020).

### Added

- **`ChunkedRequirementAnalyzer`** — a drop-in replacement for
  `RequirementAnalyzer` at pipeline position 0, with the same signature *and
  the same stage name*, so error messages and pipeline introspection are
  unchanged. It plans chunks, runs the existing analyzer (unmodified prompts,
  no variants) on each, and merges the results. Documents shorter than
  `chunk_size` delegate straight through, so small-document behavior and call
  counts are identical to before.
- **`ChunkPlanner`** — deterministic text splitting with no QA-specific logic.
  Prefers markdown headings, then paragraph breaks, then line breaks, then a
  hard cut, so a requirement is not sliced mid-sentence. Configurable size and
  overlap; guaranteed forward progress; full document coverage with no gaps.
- **`merge_requirements`** — deduplicates requirements across overlapping
  chunks by normalized title, keeps the richer of two duplicates so detail is
  not lost, preserves first-appearance order, and assigns one fresh gap-free
  `REQ-001..` sequence. Returns the full document as `source_text`, not a
  chunk.
- **Settings:** `chunk_size` (default 6000) and `chunk_overlap` (default 500),
  with cross-field validation that overlap must be smaller than size. Both
  accepted in `qaops.yaml`.
- **Tests:** 28 new (288 total) — planner determinism, overlap, boundary
  preference, hard-cut fallback, and validation; merge deduplication, ID
  reassignment, richer-duplicate retention and ordering; analyzer delegation
  for small documents, per-chunk calls, tolerance of empty chunks, failure
  when every chunk is empty; and a full-pipeline test proving downstream
  stages receive the same model types.
- **ADR-020:** chunking is internal to requirement analysis.

### Changed

- All four pipeline builders now compose `ChunkedRequirementAnalyzer`.
  Business rules, gaps, scenarios, test cases, coverage, and exporters are
  **unchanged** — the existing suite passes untouched.
- Evaluation mode (ADR-019) remains available but is no longer required for
  large documents.
- Package version 0.10.0 → 0.11.0.

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
