# ADR-040: Execution checkpointing, partial artifacts, and resume

**Status:** Accepted · **Date:** 2026-08-02 · **Relates to:** ADR-018 (ingestion), ADR-032 (frontend), ADR-035 (failure observability)

## Context

A design run executes a chain of stages (RequirementAnalyzer through Coverage).
Before Phase 25, stages chained purely in memory (`current = stage.run(current)`)
and the exporters ran only on the final `TestDesignResult`. A failure at stage N
therefore discarded the in-memory outputs of stages 1..N-1 and wrote no
artifacts — the user received nothing, and re-running restarted from scratch,
re-paying for every completed stage. Production surfaced exactly this: a run that
failed late lost all completed work.

## Decision

Add an orchestration-layer checkpoint/resume capability. The pipeline stages,
the `AdaptiveExecutor` failover semantics, and the provider architecture are
unchanged; every change is additive.

### Cumulative snapshots make this cheap

Stage outputs are cumulative and nested: `ScenarioDesignResult` embeds the
requirement analysis, `ConditionDesignResult` embeds the scenario design, and so
on. So the latest checkpoint is a complete snapshot of progress-so-far, and it
doubles as both the partial-export source and the exact resume input for the
next stage. No cross-stage stitching is needed.

### CheckpointStore (new, deterministic)

`qaops/execution/checkpoint.py` writes one JSON file per completed stage under
`<workspace>/output/checkpoints/NN_<stage>.json`, plus a `manifest.json` of
ordered stage statuses. Writes are atomic (temp-then-replace) so a crash cannot
leave a half-written checkpoint. Rehydration validates the JSON back into the
exact Pydantic model via `model_validate`. Corrupt, missing, or unknown-type
checkpoints raise `CheckpointError` rather than producing a wrong model.

### source_text is excluded from checkpoints

Because outputs are cumulative, naively serializing each stage duplicated the
entire `source_text` (the raw document) in every checkpoint — measured at ~7x on
a typical run, ~12.7x total disk vs. the final snapshot on a large one. The raw
text is already persisted in the run's `input/` dir and is read only by the
first stage; no downstream stage or exporter uses it. It is therefore stripped
from every checkpoint payload (recursively, since it appears nested) and a short
placeholder is re-injected on rehydration to satisfy the required field. This
removes the dominant redundancy with zero effect on resume or partial export.
Delta-only checkpoints (storing just each stage's new layer) were considered and
deliberately deferred as a future optimization — runs are short-lived and
workspace-scoped, so the source_text exclusion captures the large win at
negligible complexity.

### Executor hook (minimal, non-invasive)

The `AdaptiveExecutor` gains an optional `checkpoint` callback and a
`start_index`. After a stage completes it calls the sink with
`(stage_name, index, output)`; on resume it starts the loop at `start_index`.
Both default to no-op / 0, so the CLI and existing tests behave exactly as
before and provider failover is untouched.

### Partial artifacts on failure

When a `StageError` propagates, the service promotes the latest checkpoint to a
partial `TestDesignResult` (only `source_name` is required; absent dimensions
stay empty) and writes the CSV-bundle and partial JSON for the dimensions that
exist. Only successfully completed stages are ever exported — a stage that raised
never wrote a checkpoint, so its dimension is absent and no half-computed
downstream artifact can appear.

### Resume

`DesignService.resume()` loads the latest checkpoint, computes the resume index
against the entry point's stage list, and runs only the remaining stages,
feeding the rehydrated model as input. Completed stages are reused, never
re-run. If no checkpoint exists it falls back to a full run.

### Run state (additive)

`RunStatus` gains `PARTIALLY_COMPLETED`, `RESUMABLE`, `CANCELLED` (the original
four are unchanged). `Run` gains `stage_statuses`, `resumable`,
`cancel_requested`, `started_at`, `finished_at`. New endpoints
`POST /runs/{id}/resume` and `POST /runs/{id}/cancel`; the status response
surfaces per-stage statuses, `resumable`, and `completed_stages`. The UI shows a
partial-completion state, the completed stages, per-artifact downloads, and a
Resume button.

## Scope: in-process resume only

Phase 25 implements in-process resume with disk checkpoints. Reconstructing the
in-memory run registry after a **server restart** is explicitly out of scope and
left as a future enhancement. Two consequences follow, documented for honesty:

- Because resume is same-process/same-version, checkpoints are **version-local**.
  A checkpoint written by one code version is not guaranteed to rehydrate under a
  different one (schema evolution → `model_validate` would raise `CheckpointError`).
  Within a single running process this cannot occur.
- **Cancellation is cooperative**: it is honoured at stage boundaries, so a run
  mid-LLM-call stops after the current stage rather than instantly.

## Consequences

- A late failure no longer discards completed work: partial artifacts are
  downloadable immediately and the run can resume from the last checkpoint.
- The success path is unchanged (checkpoints are written silently; no exporter
  behaviour changes) and fully backward compatible.
- Checkpoint disk footprint is proportional to generated artifacts, not the raw
  document, after the source_text exclusion.

## Alternatives considered

- **Persist nothing / re-run from scratch** (status quo): rejected — loses
  completed work and re-pays for it.
- **Store the full cumulative model per stage**: rejected — the source_text
  duplication is wasteful; excluding it is a one-line-scope win.
- **Delta-only checkpoints**: deferred — more code for a footprint already made
  small by the source_text exclusion, on short-lived workspace-scoped runs.
- **Restart-resilient registry rebuild from disk**: deferred to a future phase
  per the approved scope.
