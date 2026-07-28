# ADR-032: A thin React frontend over the existing API, typed from the real contract

**Status:** Accepted · **Date:** 2026-07-27 · **Relates to:** ADR-028 (FastAPI backend), ADR-029, ADR-030

## Context

The QAOps pipeline has been reachable only through the CLI and the HTTP API. A
browser UI lowers the barrier for non-CLI users: upload a requirement document,
watch the run progress, read the results. The backend already exposes
everything needed (ADR-028): upload, run status with structured progress, and
downloadable artifacts. The frontend must consume that contract exactly, add no
backend endpoints, and never become a second source of truth for pipeline
behavior.

The main risk in a separate frontend is **contract drift** — inventing field
names, enum values, or shapes that do not match the Pydantic models, so the UI
compiles and looks right but breaks against the real backend.

## Decision

Build a Vite + React + TypeScript single-page app in `frontend/`, kept
deliberately thin, and derive every type from the *actual* API contract.

1. **Types come from the real OpenAPI schema and domain models, not guesswork.**
   `src/api/types.ts` mirrors the FastAPI response models
   (`HealthResponse`, `ModelsResponse`, `RunStatusResponse`, `ProgressSchema`,
   `SummarySchema`, `ArtifactsResponse`) and the JSON-exported domain shapes
   (`DesignArtifact` and its `requirements`, `business_rules`, `gap_report`,
   `scenarios`, `test_cases`, `coverage`). Enum values are the real ones —
   gap severity is `blocker | major | minor`, not an invented `high/medium/low`;
   priority is `critical | high | medium | low`. These were read from the
   running schema and a generated artifact, so the types match the bytes the
   backend sends.

2. **One typed API layer.** `src/api/client.ts` is the only place that calls
   `fetch`. It centralizes the base URL (`VITE_API_BASE_URL`, defaulting to the
   local backend), turns non-2xx responses into a structured `ApiError` carrying
   the backend's `detail`, raises `NetworkError` when the backend is
   unreachable, and preserves `AbortError` so cancellation is distinguishable
   from an outage. Components never touch `fetch`.

3. **Polling, not new transport.** The backend runs design asynchronously and
   returns `202` with a run id (ADR-028); there is no push channel, and adding
   WebSockets/SSE is a non-goal. `useRun` polls `GET /runs/{id}` every ~2s while
   queued/running and stops on a terminal status. It guarantees no overlapping
   requests (each poll awaits before scheduling the next), aborts the in-flight
   request and clears the timer on unmount or run-id change, and survives a
   transient failure by surfacing a soft warning and retrying rather than
   stranding the user.

4. **Progress reflects the real accounting.** The run view shows the structured
   progress the backend now emits (ADR-029/030): current stage and step,
   provider, model, `provider_call_number` (actual provider calls),
   `recovery_attempts`, and the human `message`. It renders the backend's
   values; it does not recompute or infer them. A failed run shows
   `failed_stage`, the backend error text (already secret-redacted server-side),
   and the recovery-action count.

5. **Results come from the JSON artifact.** On completion the run view fetches
   the run's JSON artifact and renders six tabbed views (requirements, business
   rules, scenarios, test cases, gaps, coverage) with search and blocker-first
   gap sorting. The artifact is the same file the CLI produces, so the UI adds
   no interpretation the backend didn't already make.

6. **CORS needed no backend change.** Phase 16 already allowed
   `localhost:5173`/`127.0.0.1:5173` (the Vite dev origins), so the frontend
   integrates without touching the API.

## Consequences

- The frontend is a presentation layer, not a second implementation. Pipeline
  behavior, bounds, retries, and redaction all remain the backend's job; the UI
  displays what the backend reports.
- Contract drift is guarded by types read from the live schema and by fixtures
  (`src/test/`) whose shapes match the OpenAPI models exactly, so tests exercise
  the true contract with no live provider calls.
- **Known gap unrelated to this ADR:** the OpenRouter client references an
  "ADR-031" (hard wall-clock deadline via `run_with_deadline`) that has no
  corresponding document in `docs/adr/`. That is a documentation gap from the
  phase that added the deadline mechanism, noted here but not filled by the
  frontend work.
- The app is intentionally minimal (no auth, router-based two-page flow, no
  persistent client state). Those are non-goals for this phase and can be added
  later without reworking the API layer.
