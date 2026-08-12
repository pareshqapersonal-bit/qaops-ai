"""FastAPI application exposing QAOps over HTTP (ADR-028).

Another interface to QAOps, not a second implementation. Every endpoint calls
existing services: model discovery goes to ModelRegistry, design runs go to
DesignService, classification and parsing reuse Phase 14. The API layer only
translates HTTP to those calls and back.

Run locally:  uvicorn qaops.api.app:app --reload
"""

import logging
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated

from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile
from fastapi import File as FileParam
from fastapi import Form as FormParam
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from qaops.api.config import APIConfig
from qaops.api.runner import execute_run, resume_run
from qaops.api.runs import RunStatus, RunStore
from qaops.api.schemas import (
    ArtifactSchema,
    ArtifactsResponse,
    AttemptSchema,
    ExecutionPlanSchema,
    HealthResponse,
    LoopSummarySchema,
    ModelSchema,
    ModelsResponse,
    ProgressSchema,
    ProviderModelsSchema,
    ReflectionSchema,
    ReviewAdviceSchema,
    ReviewReportSchema,
    RunCreatedResponse,
    RunStatusResponse,
    StageStatusSchema,
    SummarySchema,
    TicketRequest,
)
from qaops.cli.config_loader import load_settings
from qaops.core.errors import DocumentLoadError, UnsupportedDocumentFormatError
from qaops.execution import ModelRegistry, available_providers
from qaops.ingestion.registry import load_document
from qaops.ingestion.ticket_normalizer import (
    AttachmentEvidence,
    append_reference_materials,
    ticket_to_markdown,
)
from qaops.services import DesignService

logger = logging.getLogger(__name__)

# Input extensions QAOps accepts, across the document and structured routes.
_ALLOWED_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".markdown", ".csv", ".json", ".xlsx", ".xlsm"}

# Phase 35: design / reference attachments on a ticket are deliberately a NARROWER
# set than the full document-upload formats - the natural design/reference formats
# only. Not exposing csv/json/xlsx as "design attachments" just because ingestion
# supports them; can expand later on real demand.
_TICKET_ATTACHMENT_SUFFIXES = {".pdf", ".docx", ".md", ".markdown", ".txt"}


def _package_version() -> str:
    try:
        return version("qaops-ai")
    except PackageNotFoundError:  # pragma: no cover - installed in all environments here
        return "unknown"


def _sanitize_filename(name: str) -> str:
    """Reduce an uploaded filename to a safe basename.

    Trusting the upload's name for a path invites traversal. We keep only the
    final component and strip anything that is not a safe character, so a run's
    input file lands predictably inside its own workspace.
    """
    base = Path(name).name  # drops any directory components
    cleaned = "".join(c for c in base if c.isalnum() or c in "._- ").strip()
    return cleaned or "upload"


def _mount_frontend(app: FastAPI, static_dir: Path | None) -> None:
    """Serve the built React frontend (Vite output) for the browser UI.

    Registered AFTER all API routes so those always take precedence: FastAPI
    matches routes in registration order, and the SPA catch-all additionally
    refuses any path under the API surface, so an unknown ``/api/*`` request
    returns a real API 404 (JSON) rather than the SPA's index.html, and
    ``/health`` keeps returning the backend response (spec parts 1-3).

    Static assets (``/assets/...``) are served from disk. Any other GET that is
    not an API path falls back to ``index.html`` so React Router can handle
    ``/``, ``/design`` and ``/runs/{id}`` on direct navigation or refresh.

    If the build is absent (``static_dir`` missing - e.g. a pure-API deployment
    or a test that never built the frontend), the API stays fully functional and
    non-API browser routes return a clear, controlled message instead of a
    confusing 500 or a silent break. API routes are unaffected either way.
    """
    index_file = static_dir / "index.html" if static_dir is not None else None
    build_present = index_file is not None and index_file.is_file()

    # Reserved server-side prefixes that must never be answered by the SPA.
    def _is_api_path(path: str) -> bool:
        return path == "health" or path == "api" or path.startswith("api/")

    if build_present:
        assert static_dir is not None  # narrowed by build_present
        assets_dir = static_dir / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/", include_in_schema=False)
        def _spa_root() -> FileResponse:
            return FileResponse(index_file)  # type: ignore[arg-type]

        @app.get("/{full_path:path}", include_in_schema=False)
        def _spa_fallback(full_path: str) -> FileResponse:
            # API paths are handled by their own routes; if control reaches here
            # for an api/ or health path, it is an UNKNOWN one and must 404 as an
            # API response, never as index.html.
            if _is_api_path(full_path):
                raise HTTPException(status_code=404, detail="Not found.")
            # A concrete static file (favicon, etc.) if it exists on disk.
            candidate = (static_dir / full_path).resolve()
            static_root = static_dir.resolve()
            if static_root in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            # Otherwise it is a client-side route: serve the SPA shell.
            return FileResponse(index_file)  # type: ignore[arg-type]

    else:

        @app.get("/", include_in_schema=False)
        def _no_build_root() -> PlainTextResponse:
            return PlainTextResponse(
                "QAOps API is running, but the frontend build was not found. "
                "Build it with `npm ci && npm run build` in ./frontend, or set "
                "QAOPS_STATIC_DIR to the build output. The API remains available "
                "under /health and /api/v1/.",
                status_code=503,
            )

        @app.get("/{full_path:path}", include_in_schema=False)
        def _no_build_fallback(full_path: str) -> PlainTextResponse:
            # Even without a build, API paths must behave as API paths: an
            # unknown one is a 404, not this degraded-frontend notice.
            if _is_api_path(full_path):
                raise HTTPException(status_code=404, detail="Not found.")
            return PlainTextResponse(
                "QAOps frontend build not found. The API remains available "
                "under /health and /api/v1/.",
                status_code=503,
            )


def create_app(config: APIConfig | None = None) -> FastAPI:
    """Build the application. A factory so tests can inject a config."""
    cfg = config or APIConfig()
    store = RunStore(cfg.runtime_dir)
    registry = ModelRegistry()
    service = DesignService(registry=registry)

    app = FastAPI(
        title="QAOps AI API",
        version=_package_version(),
        description=(
            "HTTP interface to QAOps AI. Upload a requirement document or a "
            "requirements/scenarios artifact and QAOps designs test cases, "
            "detecting the workflow automatically. Runs execute asynchronously."
        ),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    # Stash shared objects for tests and handlers.
    app.state.store = store
    app.state.registry = registry
    app.state.service = service
    app.state.config = cfg

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    def health() -> HealthResponse:
        """Liveness check. Makes no LLM call and reads no secrets."""
        return HealthResponse(status="ok", service="qaops-ai", version=_package_version())

    @app.get("/api/v1/models", response_model=ModelsResponse, tags=["discovery"])
    def list_models(refresh: bool = False) -> ModelsResponse:
        """List models each available provider can serve.

        Delegates to ModelRegistry (Phase 15). Availability is decided by the
        presence of a provider's credential; the credential itself is never
        read into the response.
        """
        if refresh:
            registry.refresh()
        providers: list[ProviderModelsSchema] = []
        for info in available_providers():
            discovered = registry.models_for(info.name)
            source = "static"
            discovered_at = registry.discovered_at(info.name)
            if discovered_at is not None:
                source = "cache"
            providers.append(
                ProviderModelsSchema(
                    provider=info.name,
                    source=source,
                    models=[
                        ModelSchema(
                            id=m.name,
                            max_context_tokens=m.max_context_tokens,
                            max_output_tokens=m.max_output_tokens,
                            structured_output=m.structured_output,
                            local=m.local,
                            free=m.free,
                        )
                        for m in discovered
                    ],
                )
            )
        return ModelsResponse(providers=providers)

    def _create_and_schedule_run(
        *, input_name: str, contents: bytes, suffix: str, background: BackgroundTasks
    ) -> RunCreatedResponse:
        """Create a run, persist the input, schedule execution, return at once.

        The source-agnostic run-creation tail shared by every input source (Phase
        32). It depends only on a resolved (input_name, contents, suffix) - never on
        how those were obtained - so both the document upload and the ticket
        endpoint reuse it without duplicating run creation, file persistence,
        scheduling, or the response. The workflow (document / requirements /
        scenarios) is still detected downstream from the written file; callers do
        not specify it.
        """
        run = store.create(input_name=input_name)
        safe_name = _sanitize_filename(input_name)
        # Preserve the extension even if sanitizing altered the stem.
        if not safe_name.lower().endswith(suffix):
            safe_name = f"{Path(safe_name).stem}{suffix}"
        (run.input_dir / safe_name).write_bytes(contents)

        settings = load_settings(None)
        background.add_task(execute_run, store, run.id, settings, service)
        return RunCreatedResponse(run_id=run.id, status=run.status.value)

    @app.post(
        "/api/v1/design",
        response_model=RunCreatedResponse,
        status_code=202,
        tags=["design"],
    )
    async def submit_design(
        background: BackgroundTasks,
        file: Annotated[UploadFile, FileParam(description="Requirement or scenario file")],
    ) -> RunCreatedResponse:
        """Accept an upload, create a run, schedule execution, return at once.

        The workflow (document / requirements / scenarios) is detected from the
        file; the caller does not specify it.
        """
        raw_name = file.filename or "upload"
        suffix = Path(raw_name).suffix.lower()
        if suffix not in _ALLOWED_SUFFIXES:
            allowed = ", ".join(sorted(_ALLOWED_SUFFIXES))
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported input type {suffix or '(none)'}. Supported: {allowed}.",
            )

        contents = await file.read()
        if not contents:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        return _create_and_schedule_run(
            input_name=raw_name, contents=contents, suffix=suffix, background=background
        )

    @app.post(
        "/api/v1/design/ticket",
        response_model=RunCreatedResponse,
        status_code=202,
        tags=["design"],
    )
    async def submit_ticket(
        background: BackgroundTasks,
        title: Annotated[str, FormParam(min_length=1)],
        description: Annotated[str, FormParam(min_length=1)],
        ticket_id: Annotated[str | None, FormParam()] = None,
        priority: Annotated[str | None, FormParam()] = None,
        acceptance_criteria: Annotated[list[str] | None, FormParam()] = None,
        labels: Annotated[list[str] | None, FormParam()] = None,
        attachment: Annotated[list[UploadFile] | None, FileParam()] = None,
    ) -> RunCreatedResponse:
        """Accept a Jira-style ticket + optional design/reference attachments (Phase 35).

        The ticket is transcribed to Markdown by the TicketNormalizer. Each supplied
        attachment (0, 1, or many - multipart field name "attachment") is extracted
        via the EXISTING load_document ingestion and appended as its own delimited
        "Design / Reference Material" section, in upload order - additional document
        EVIDENCE, never parsed into requirements here. The combined single .md flows
        through the EXISTING DOCUMENT pipeline via the same run-creation helper. No
        second pipeline, no Jira integration. Provenance rides the input name
        ("<TICKET-ID> - <title>", or title alone) into source_name; each attachment's
        own name is recorded only in its evidence section.

        Attachment handling is strict: if ANY attachment fails validation or
        extraction, the whole request fails with a client-facing 400 (never a bare
        500, and never a silent skip). Without attachments this is byte-identical to
        the Phase 32 ticket-only pipeline path; with exactly one it is byte-identical
        to Phase 35A.
        """
        ticket = TicketRequest(
            title=title,
            description=description,
            ticket_id=ticket_id,
            priority=priority,
            acceptance_criteria=acceptance_criteria or [],
            labels=labels or [],
        )
        markdown = ticket_to_markdown(ticket)

        # Each attachment: validate, extract via existing ingestion, collect as
        # evidence in upload order. Strict-fail - the first bad file raises 400 and
        # no run is created; failures name the offending file and never surface as
        # a 500.
        evidences: list[AttachmentEvidence] = []
        for item in attachment or []:
            if not item.filename:
                continue
            raw_name = item.filename
            suffix = Path(raw_name).suffix.lower()
            if suffix not in _TICKET_ATTACHMENT_SUFFIXES:
                allowed = ", ".join(sorted(_TICKET_ATTACHMENT_SUFFIXES))
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Unsupported attachment type {suffix or '(none)'} for "
                        f"'{raw_name}'. Design / reference attachments must be one of: "
                        f"{allowed}."
                    ),
                )
            attachment_bytes = await item.read()
            if not attachment_bytes:
                raise HTTPException(status_code=400, detail=f"Attachment '{raw_name}' is empty.")

            with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
                tmp.write(attachment_bytes)
                tmp.flush()
                try:
                    attachment_text = load_document(Path(tmp.name))
                except (UnsupportedDocumentFormatError, DocumentLoadError) as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Attachment '{raw_name}' could not be processed: {exc}",
                    ) from exc

            if not attachment_text.strip():
                raise HTTPException(
                    status_code=400,
                    detail=f"Attachment '{raw_name}' contains no extractable text.",
                )
            evidences.append(AttachmentEvidence(filename=raw_name, text=attachment_text))

        markdown = append_reference_materials(markdown, evidences)

        if ticket.ticket_id and ticket.ticket_id.strip():
            input_name = f"{ticket.ticket_id.strip()} - {ticket.title.strip()}.md"
        else:
            input_name = f"{ticket.title.strip()}.md"
        return _create_and_schedule_run(
            input_name=input_name,
            contents=markdown.encode("utf-8"),
            suffix=".md",
            background=background,
        )

    @app.post(
        "/api/v1/runs/{run_id}/resume",
        response_model=RunCreatedResponse,
        status_code=202,
        tags=["design"],
    )
    def resume_design(run_id: str, background: BackgroundTasks) -> RunCreatedResponse:
        """Resume a resumable run from its last checkpoint (ADR-040).

        Only runs marked resumable (a stage failed with completed work behind it)
        can resume; completed stages are reused, not re-run.
        """
        run = store.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"No run with id {run_id!r}.")
        if not run.resumable:
            raise HTTPException(
                status_code=409,
                detail=f"Run {run_id!r} is not resumable (status {run.status.value}).",
            )
        settings = load_settings(None)
        background.add_task(resume_run, store, run.id, settings, service)
        return RunCreatedResponse(run_id=run.id, status=RunStatus.RUNNING.value)

    @app.post(
        "/api/v1/runs/{run_id}/cancel",
        response_model=RunStatusResponse,
        tags=["design"],
    )
    def cancel_design(run_id: str) -> RunStatusResponse:
        """Request cooperative cancellation of a run (ADR-040).

        Cancellation is cooperative: a run stops at the next stage boundary. A
        run that has already finished is returned unchanged.
        """
        run = store.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"No run with id {run_id!r}.")
        if run.status in (RunStatus.QUEUED, RunStatus.RUNNING):
            store.update(run_id, cancel_requested=True, status=RunStatus.CANCELLED)
        return run_status(run_id)

    @app.get("/api/v1/runs/{run_id}", response_model=RunStatusResponse, tags=["design"])
    def run_status(run_id: str) -> RunStatusResponse:
        """Current state of a run, with a summary once completed."""
        run = store.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"No run with id {run_id!r}.")
        summary = None
        if run.summary is not None:
            s = run.summary
            summary = SummarySchema(
                requirements=int(s["requirements"]),
                business_rules=int(s["business_rules"]),
                scenarios=int(s["scenarios"]),
                test_conditions=int(s.get("test_conditions", 0)),
                test_cases=int(s["test_cases"]),
                gaps=int(s["gaps"]),
                coverage_percent=float(s["coverage_percent"]),
                requirement_coverage_percent=float(
                    s.get("requirement_coverage_percent", s["coverage_percent"])
                ),
                business_rule_coverage_percent=float(s.get("business_rule_coverage_percent", 0.0)),
                scenario_coverage_percent=float(s.get("scenario_coverage_percent", 0.0)),
                condition_coverage_percent=float(s.get("condition_coverage_percent", 0.0)),
                unresolved_conditions=int(s.get("unresolved_conditions", 0)),
                expansion_truncated=bool(s.get("expansion_truncated", 0)),
            )
        # Progress is meaningful while running and preserved after, so a
        # completed/failed run still shows the final stage state (section 13).
        exec_state = run.execution
        progress = None
        if exec_state.current_stage is not None:
            progress = ProgressSchema(
                current_stage=exec_state.current_stage,
                stage_index=exec_state.stage_index,
                stage_count=exec_state.stage_count,
                provider=exec_state.provider,
                model=exec_state.model,
                model_attempt_number=exec_state.model_attempt_number,
                request_attempt=exec_state.request_attempt,
                provider_call_number=exec_state.provider_call_number,
                models_attempted=exec_state.models_attempted,
                recovery_attempts=exec_state.recovery_attempts,
                message=exec_state.message,
            )
        return RunStatusResponse(
            run_id=run.id,
            status=run.status.value,
            entry_point=run.entry_point,
            detection=run.detection,
            summary=summary,
            progress=progress,
            error=run.error,
            failed_stage=run.failed_stage,
            recovery_attempts=run.recovery_attempts or None,
            attempt_history=(
                [
                    AttemptSchema(
                        stage=str(a.get("stage", "")),
                        provider=str(a.get("provider", "")),
                        model=str(a.get("model", "")),
                        failure_kind=str(a.get("failure_kind", "")),
                        status_code=(sc if isinstance((sc := a.get("status_code")), int) else None),
                        error_code=(
                            str(a.get("error_code")) if a.get("error_code") is not None else None
                        ),
                    )
                    for a in run.attempt_history
                ]
                if run.attempt_history
                else None
            ),
            stage_statuses=(
                [
                    StageStatusSchema(
                        stage=str(s.get("stage", "")),
                        status=str(s.get("status", "")),
                        started_at=(
                            str(s["started_at"]) if s.get("started_at") is not None else None
                        ),
                        finished_at=(
                            str(s["finished_at"]) if s.get("finished_at") is not None else None
                        ),
                    )
                    for s in run.stage_statuses
                ]
                if run.stage_statuses
                else None
            ),
            resumable=run.resumable,
            completed_stages=(
                [
                    str(s.get("stage", ""))
                    for s in run.stage_statuses
                    if s.get("status") == "completed"
                ]
                if run.stage_statuses
                else None
            ),
            plan=(ExecutionPlanSchema.model_validate(run.plan) if run.plan else None),
            reflection=(
                ReflectionSchema.model_validate(run.reflection) if run.reflection else None
            ),
            loop_summary=(
                LoopSummarySchema.model_validate(run.loop_summary) if run.loop_summary else None
            ),
            review=(ReviewReportSchema.model_validate(run.review) if run.review else None),
            review_advice=(
                ReviewAdviceSchema.model_validate(run.review_advice) if run.review_advice else None
            ),
        )

    @app.get("/api/v1/runs/{run_id}/artifacts", response_model=ArtifactsResponse, tags=["design"])
    def run_artifacts(run_id: str) -> ArtifactsResponse:
        """Metadata for the reports a completed run produced."""
        run = store.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"No run with id {run_id!r}.")
        return ArtifactsResponse(
            run_id=run.id,
            artifacts=[ArtifactSchema(name=a.name, format=a.format) for a in run.artifacts],
        )

    @app.get("/api/v1/runs/{run_id}/artifacts/{artifact_name}", tags=["design"])
    def download_artifact(run_id: str, artifact_name: str) -> FileResponse:
        """Download one report file, resolved strictly within the run's output.

        The filename is matched against the run's known artifacts and the
        resolved path is confirmed to sit inside the output directory, so a
        crafted name like `../../etc/passwd` cannot escape the workspace.
        """
        run = store.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"No run with id {run_id!r}.")

        known = {a.name: a for a in run.artifacts}
        artifact = known.get(artifact_name)
        if artifact is None:
            raise HTTPException(status_code=404, detail=f"No artifact {artifact_name!r} in run.")

        output_root = run.output_dir.resolve()
        try:
            resolved = artifact.path.resolve()
        except OSError as exc:  # pragma: no cover - defensive
            raise HTTPException(status_code=404, detail="Artifact is not accessible.") from exc
        if output_root not in resolved.parents or not resolved.is_file():
            # Should never happen for a registered artifact, but the check is
            # the barrier against traversal regardless of how the name arrived.
            raise HTTPException(status_code=404, detail="Artifact is not accessible.")

        return FileResponse(path=resolved, filename=artifact.name)

    _mount_frontend(app, cfg.static_dir)

    return app


app = create_app()
