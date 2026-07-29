# ADR-033: Single-service production deployment on Render

**Status:** Accepted · **Date:** 2026-07-28 · **Relates to:** ADR-028 (FastAPI backend), ADR-032 (frontend)

## Context

QAOps needs to be usable from another machine through one public URL, with no
local Python/Node/Vite/Uvicorn install. Phase 17 produced a React SPA (ADR-032)
that talks to the FastAPI backend (ADR-028). For a free hosting tier the
simplest reliable shape is a single web service rather than two coordinated
deployments.

## Decision

Deploy as **one Render Web Service** in which FastAPI serves both the API and
the built React frontend from the same origin.

### FastAPI serves the Vite build

`create_app` registers all API routes first, then calls `_mount_frontend`, which:

- mounts `/assets` from the build's `assets/` directory for JS/CSS;
- serves `index.html` at `/` and for any non-API path (SPA fallback), so React
  Router handles `/`, `/design`, and `/runs/{id}` on direct navigation and
  refresh;
- serves a concrete static file (e.g. `favicon.ico`) when one exists on disk.

Because the frontend is mounted **after** the API routes and the SPA catch-all
explicitly refuses any `health` or `api/` path, the API always takes
precedence: an unknown `/api/*` request returns a real API 404 (JSON), never
`index.html`, and `/health` always returns the backend health JSON. This is the
critical routing invariant and it is covered by tests.

The build location is `APIConfig.static_dir`, defaulting to `frontend/dist`
(overridable via `QAOPS_STATIC_DIR`). If the build is absent, the API stays
fully functional and browser routes return a clear `503` notice instead of a
confusing error - a controlled degradation, not a silent break.

### Same-origin frontend API base

The frontend's API base URL defaults to `""` (empty), so production requests are
relative and same-origin (`/api/v1/...`). No backend host is baked into the
bundle. `VITE_API_BASE_URL` still works for alternative setups. Local
development keeps the split origin via `frontend/.env.development`
(`VITE_API_BASE_URL=http://localhost:8000`), which Vite loads only in dev;
`frontend/.env.production` sets it empty. The existing Vite (`:5173`) + FastAPI
(`:8000`) workflow is unchanged.

### Build at deploy time

`frontend/dist` and `frontend/node_modules` are not committed. Render builds the
frontend during deployment (`npm ci && npm run build`) after installing the
Python package with its API and provider extras. `render.yaml` versions this.

### Configuration and secrets

Production configures the pipeline through `QAOPS_*` environment variables and
provider keys set server-side in Render; it does **not** depend on a developer's
repo-root `qaops.yaml`, which is now gitignored so it cannot reach production.
`render.yaml` declares provider-key variable **names** with `sync: false`;
values are entered in the Render dashboard and never committed. No secret
appears in the frontend bundle, API responses, progress events, or artifacts -
verified by a test that plants sentinel keys and asserts their absence from
served assets.

### CORS

Production is same-origin, so CORS is not needed for the deployed browser flow.
The existing development CORS origins (localhost Vite) are retained unchanged.

## Consequences

- One service, one URL, one build - the simplest deployable shape; no Docker, no
  split services, no orchestration.
- Run state, uploads, and artifacts live on Render Free's **ephemeral**
  filesystem and in memory. They are temporary and may disappear on restart,
  redeploy, or idle spin-down. Acceptable for the MVP; documented as a
  limitation. Per-run workspace isolation is unchanged.
- Runtimes are pinned for reproducibility (Python 3.12, Node 22) without
  upgrading any framework or dependency.
- The failure view now shows a concise summary with the raw provider error
  tucked into an optional details disclosure - presentation only, no change to
  pipeline or recovery behaviour.

## Alternatives considered

- **Two services (static site + API):** more moving parts, cross-origin CORS,
  two URLs. Rejected - the single service is simpler and meets the goal.
- **Docker:** unnecessary; Render's native Python + Node build covers it.
