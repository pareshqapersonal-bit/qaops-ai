# ADR-061: Clarification frontend (Phase 41D)

**Status:** Accepted · **Date:** 2026-08-14 · **Phase 41D** · **Relates to:** ADR-059/060 (41C), Phase 41D review

## Context

Phases 41A-41C built the clarification state, agent, and API/handoff. 41D adds the UI so
a user can opt into clarification, answer questions, see readiness, and start test design
- reusing the existing run page and the entire completed/failed/one-shot rendering path.

## Decision

- **Opt-in checkbox** ("Clarify requirements first") on both submit paths (file and
  ticket), passing `clarify=true`. Off by default -> existing one-shot flow unchanged.
- **Backend ticket parity**: the `clarify` flag, previously only on `POST /design`
  (41C), is added to `POST /design/ticket` (a one-line FormParam through the existing
  `_create_and_schedule_run` helper) so both entry points support clarification
  (approved decision 1).
- **RunPage branches, not a new page** (decision 2): two new status branches -
  `awaiting_clarification` -> `<ClarificationPanel>`, `ready_for_test_design` ->
  `<ReadinessGate>` - via a small `ClarificationView` wrapper. All other branches
  (failed/partial/cancelled/completed/running -> ProgressView/Results) are unchanged.
  After start-test-design the run moves to running and the EXISTING ProgressView/Results
  path takes over.
- **`useClarification` hook** (decision 3) owns the question batch + readiness + local
  answer selections; `useRun` keeps owning run-status polling (it already handles the
  non-terminal clarification statuses correctly).
- **Answer widgets by type**: boolean -> Yes/No buttons, single_select -> radios,
  multi_select -> checkboxes, numeric/date -> inputs, text -> textarea (rare). Blocking
  questions first; batch submit.
- **Proceed-with-assumptions** surfaces only when no blocking questions remain
  (decision 4); a 5-round cap 409 shows the backend detail. Answers stay editable until
  start (decision 5).
- **Errors** reuse the existing ApiError/NetworkError surface: 409 -> the run moved on
  (re-branch via useRun); 400 -> contradictory/malformed detail inline.

## Consequences

- End-to-end UI: upload (clarify) -> answer questions -> readiness -> Generate Test
  Cases -> existing progress/results. The test-design half of the UI is reused unchanged.
- One-shot flow byte-identical when clarify is unchecked (verified: createDesignRun/
  createTicketRun omit the flag; the existing tests still pass).
- No change to Results, ProgressView, the useRun core loop, or any backend pipeline/
  executor/agent code.

## Scope

Frontend: `api/types.ts`, `api/client.ts`, `hooks/useClarification.ts` (new),
`components/ClarificationPanel.tsx` + `ReadinessGate.tsx` (new), `pages/RunPage.tsx`,
`pages/UploadPage.tsx`, `components/common.tsx`, plus tests. Backend: one FormParam on
`submit_ticket` in `qaops/api/app.py`. Untouched: executor, selector, DesignService,
GapAnalyzer, pipeline stages, 41A models, 41B agent, 41C service/runner, providers,
image ingestion, Results, ProgressView, useRun core, and the one-shot path.
