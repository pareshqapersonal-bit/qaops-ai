# ADR-028: A FastAPI interface over a shared DesignService, not a second pipeline

**Status:** Accepted · **Date:** 2026-07-25 · **Relates to:** ADR-022, ADR-023, ADR-025, ADR-026, ADR-027

## Context

The pipeline was reachable only through the CLI. A web UI needs HTTP access,
but the phase's hard constraint is that FastAPI must not reimplement QAOps —
the CLI and API should run the *same* orchestration.

The obstacle was that the orchestration lived inside the CLI's `_run_design`,
interleaved with terminal output (`_echo`) and Typer error handling. The API
could not call it without inheriting those CLI concerns.

## Decision

Extract the orchestration into a `DesignService` both interfaces call, and add
a thin FastAPI layer that translates HTTP to service calls. No pipeline stage,
prompt, exporter, chunking, executor, parser, or classifier changes.

1. **DesignService owns the workflow.** Classify, preflight, parse, build the
   pipeline, run it through the adaptive executor, write reports. It reports
   progress through a callback the caller supplies — the CLI passes its echo,
   the API captures the lines into a run log — so it prints nothing and raises
   only domain errors. The CLI now calls the service and keeps its own summary
   rendering; behaviour is unchanged, proven by the existing CLI tests passing
   untouched in intent (only the mock-injection point moved, since the LLM
   client is now constructed inside the service).

2. **The extraction moved ADR-023 safety with it.** The output-collision guard
   and friendly filesystem errors were part of the CLI's report writing; they
   now live in the service, so the API gets them too. This was a genuine bug
   risk caught during extraction — omitting them would have let an API run
   overwrite an input or surface a raw `PermissionError`.

3. **The API is a translation layer.** `/api/v1/models` calls `ModelRegistry`;
   `/api/v1/design` calls `DesignService`; classification and parsing are
   Phase 14's. The API adds only: multipart handling, a run registry, HTTP
   status mapping, and artifact serving.

4. **Runs are asynchronous, with an isolated store.** A design can take
   minutes, so `POST /design` validates, creates a run, schedules background
   execution, and returns `202` immediately. The `RunStore` is an in-memory,
   thread-safe registry behind a small interface, so a persistent store or job
   queue can replace it later without touching the API. Each run owns a
   workspace (`input/` and `output/`) keyed by a generated run id, so
   concurrent runs never share files.

5. **Failures after submission become `status = failed`, not HTTP errors.**
   Once a run is accepted the POST has already returned; a later provider or
   pipeline failure is recorded on the run. Only pre-submission problems (bad
   upload, unsupported type) are 4xx on the POST itself.

6. **Secrets never leave.** Provider errors can carry an API key or auth
   header; the run's stored error is redacted (`sk-…`, `Bearer …`,
   `api-key=…` patterns → `[redacted]`) and the API returns no tracebacks. The
   models endpoint reports capabilities and availability, never the credential
   that made a provider available.

7. **Path traversal is blocked by identity, not string checks.** An artifact
   download matches the requested name against the run's *known* artifacts and
   confirms the resolved path sits inside the run's output directory. A crafted
   `../../etc/passwd` matches no known artifact and fails the containment
   check.

8. **Version comes from package metadata.** `qaops/__init__.py` carries a stale
   `__version__ = "0.1.0"`; the health endpoint reads `importlib.metadata`
   instead, so `/health` reports the real installed version.

## Consequences

- One orchestration, two interfaces. A pipeline change reaches CLI and API
  together, and neither can drift.
- FastAPI is an optional `[api]` extra; the CLI runs without it, and installing
  QAOps does not pull in a web stack unless asked.
- **Known limitation:** the run registry is in memory, so a process restart
  loses all run status. Workspaces persist on disk but the index that finds
  them does not. This is acceptable for the local, single-process scope of this
  phase and is the seam a persistent store would fill.
- **A coupling worth noting:** `load_settings` and `resolve_exporters` live
  under `qaops.cli.` but have no CLI-specific behaviour, so the service imports
  them from there. Relocating them to a neutral module would be cleaner but is
  a broader change than this phase's "no broad refactor" instruction allows;
  left as a follow-up.
- This is plumbing, not intelligence: the API plans nothing and makes no
  decisions the CLI did not already make. It exposes existing behaviour over
  HTTP.
