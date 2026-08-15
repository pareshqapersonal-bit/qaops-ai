import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ApiError,
  NetworkError,
  artifactDownloadUrl,
  getArtifacts,
  getJsonArtifact,
  resumeRun,
} from "../api/client";
import type {
  ArtifactSchema,
  DesignArtifact,
  ExecutionPlanSchema,
  LoopSummarySchema,
  ReflectionSchema,
} from "../api/types";
import { useRun } from "../hooks/useRun";
import { useClarification } from "../hooks/useClarification";
import {
  PIPELINE_STAGES,
  deriveStageStates,
} from "../hooks/useBackendStatus";
import { KeyValue, StatusBadge } from "../components/common";
import { Results } from "../components/Results";
import { ClarificationPanel } from "../components/ClarificationPanel";
import { ReadinessGate } from "../components/ReadinessGate";

export function RunPage() {
  const { runId = null } = useParams();
  const { run, loadState, errorMessage } = useRun(runId);

  if (!runId) {
    return <div className="alert error">No run id provided.</div>;
  }

  if (loadState === "loading" && !run) {
    return (
      <div className="row">
        <span className="spin" aria-hidden="true" />
        <span className="muted">Loading run {runId}…</span>
      </div>
    );
  }

  if (loadState === "error" && !run) {
    return (
      <div className="alert error" role="alert">
        <div className="title">Could not load this run</div>
        <div>{errorMessage ?? "The run could not be retrieved."}</div>
        <p style={{ marginTop: 12 }}>
          <Link to="/">← Start a new run</Link>
        </p>
      </div>
    );
  }

  if (!run) return null;

  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div>
          <h1 style={{ marginBottom: 2 }}>Run</h1>
          <div className="cell-mono muted">{run.run_id}</div>
        </div>
        <StatusBadge status={run.status} />
      </div>

      {run.detection && (
        <p className="subtitle" style={{ marginTop: 10 }}>
          Detected: {run.detection}
          {run.entry_point ? ` · entry point: ${run.entry_point}` : ""}
        </p>
      )}

      {errorMessage && run.status !== "failed" && (
        <div className="alert warn" role="status" style={{ marginTop: 12 }}>
          <div className="title">Reconnecting…</div>
          <div>{errorMessage}</div>
        </div>
      )}

      {run.plan && <ExecutionPlanPanel plan={run.plan} />}

      {run.status === "failed" ||
      run.status === "partially_completed" ||
      run.status === "cancelled" ? (
        <FailureView run={run} />
      ) : run.status === "completed" ? (
        <CompletedView runId={run.run_id} summary={run.summary} />
      ) : run.status === "awaiting_clarification" ||
        run.status === "ready_for_test_design" ? (
        <ClarificationView runId={run.run_id} status={run.status} />
      ) : (
        <ProgressView run={run} />
      )}

      {run.reflection && <ReflectionPanel reflection={run.reflection} />}

      {run.loop_summary && <LoopSummaryPanel summary={run.loop_summary} />}
    </div>
  );
}

// Phase 41D: renders the clarification loop for a run that is awaiting
// clarification or ready for test design. Owns clarification state via
// useClarification; run-status polling stays in useRun (RunPage). When status is
// awaiting_clarification the panel collects answers; when ready_for_test_design
// the gate offers Generate Test Cases. A 409 (run moved on) is a no-op here -
// useRun keeps polling and RunPage re-branches on the next status.
function ClarificationView({
  runId,
  status,
}: {
  runId: string;
  status: string;
}) {
  const {
    data,
    loadState,
    errorMessage,
    answers,
    setAnswer,
    submitting,
    submitError,
    submit,
  } = useClarification(runId, true);

  if (loadState === "loading" && !data) {
    return (
      <div className="panel" role="status">
        Loading clarification…
      </div>
    );
  }
  if (loadState === "error" && !data) {
    return (
      <div className="alert error" role="alert">
        {errorMessage ?? "Failed to load clarification."}
      </div>
    );
  }
  if (!data) return null;

  if (status === "ready_for_test_design") {
    return <ReadinessGate runId={runId} data={data} />;
  }
  return (
    <ClarificationPanel
      data={data}
      answers={answers}
      setAnswer={setAnswer}
      submitting={submitting}
      submitError={submitError}
      onSubmit={submit}
    />
  );
}

// Phase 27 (ADR-042): the goal-driven loop's decision record - iterations,
// each with its observation/decision, and the terminal reason. Additive
// transparency; absent for runs created before the loop existed.
function LoopSummaryPanel({ summary }: { summary: LoopSummarySchema }) {
  const terminalLabel: Record<string, string> = {
    completed: "Completed successfully",
    max_resume_attempts: "Stopped: resume-attempt limit reached",
    needs_clarification: "Completed, clarification recommended",
    needs_manual_review: "Stopped: manual review recommended",
  };
  return (
    <section className="card" style={{ marginTop: 16 }}>
      <h2 style={{ marginTop: 0 }}>Execution loop</h2>
      <p className="muted">
        {terminalLabel[summary.terminal_reason] ?? summary.terminal_reason} ·{" "}
        {summary.iterations.length} iteration
        {summary.iterations.length === 1 ? "" : "s"}
        {summary.resume_attempts > 0
          ? ` · ${summary.resume_attempts} resume attempt(s)`
          : ""}
      </p>
      {summary.iterations.length > 1 && (
        <details>
          <summary>Decisions ({summary.iterations.length})</summary>
          <ol style={{ marginTop: 8 }}>
            {summary.iterations.map((it) => (
              <li key={it.iteration} style={{ marginBottom: 6 }}>
                <strong>{it.decision.decision}</strong> — {it.decision.reason}
              </li>
            ))}
          </ol>
        </details>
      )}
    </section>
  );
}

// Phase 26 (ADR-041): the orchestrator agent's execution plan and the decisions
// behind it. Purely additive - reasoning about execution, shown alongside the
// existing run views. Absent for runs created before the agent existed.
function ExecutionPlanPanel({ plan }: { plan: ExecutionPlanSchema }) {
  return (
    <section className="card" style={{ marginBottom: 16 }}>
      <h2 style={{ marginTop: 0 }}>Execution plan</h2>
      <p className="muted">
        Goal: {plan.goal} · Entry point: {plan.entry_point} ·{" "}
        {plan.resume ? "Resuming from checkpoints" : "Full run"}
      </p>
      <ol style={{ marginTop: 8 }}>
        {plan.steps.map((s) => (
          <li key={s.stage} style={{ marginBottom: 4 }}>
            <strong>{s.stage}</strong>{" "}
            <span className="muted">[{s.status}]</span>
            {s.reason ? <> — {s.reason}</> : null}
          </li>
        ))}
      </ol>
      {plan.decisions.length > 0 && (
        <details style={{ marginTop: 8 }}>
          <summary>Decisions ({plan.decisions.length})</summary>
          <ul style={{ marginTop: 8 }}>
            {plan.decisions.map((d, i) => (
              <li key={i} style={{ marginBottom: 6 }}>
                <strong>{d.decision}</strong> — {d.reason}
                {d.alternative_considered ? (
                  <div className="muted">
                    Alternative: {d.alternative_considered} — rejected because{" "}
                    {d.rejected_because}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

// Phase 26 (ADR-041): the orchestrator agent's post-run reflection. Reasoning
// only - it never contains pipeline artifacts.
function ReflectionPanel({ reflection }: { reflection: ReflectionSchema }) {
  return (
    <section className="card" style={{ marginTop: 16 }}>
      <h2 style={{ marginTop: 0 }}>Execution reflection</h2>
      <p>{reflection.summary}</p>
      {reflection.recommendations.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <strong>Recommendations</strong>
          <ul>
            {reflection.recommendations.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
      )}
      {(reflection.recovered_stages.length > 0 ||
        reflection.skipped_stages.length > 0) && (
        <p className="muted">
          {reflection.skipped_stages.length > 0 && (
            <>Reused: {reflection.skipped_stages.join(", ")}. </>
          )}
          {reflection.recovered_stages.length > 0 && (
            <>Recovered after retry: {reflection.recovered_stages.join(", ")}.</>
          )}
        </p>
      )}
      {reflection.lessons.length > 0 && (
        <details style={{ marginTop: 8 }}>
          <summary>Lessons</summary>
          <ul style={{ marginTop: 8 }}>
            {reflection.lessons.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

function ProgressView({ run }: { run: NonNullable<ReturnType<typeof useRun>["run"]> }) {
  const stages = deriveStageStates(run);
  const p = run.progress;
  return (
    <>
      <section className="panel">
        <h2>Pipeline</h2>
        <div className="stepper">
          {PIPELINE_STAGES.map((s, i) => (
            <div key={s.key} className={`step ${stages[i]}`}>
              <span className="marker" aria-hidden="true">
                {stages[i] === "completed" ? "✓" : stages[i] === "failed" ? "✕" : i + 1}
              </span>
              <span className="label">{s.label}</span>
              {stages[i] === "active" && (
                <span className="muted" style={{ marginLeft: "auto" }}>
                  <span className="spin" aria-hidden="true" /> in progress
                </span>
              )}
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <h2>Live progress</h2>
        {p ? (
          <>
            <div className="kv-grid">
              <KeyValue k="Stage" v={p.current_stage ?? "—"} />
              <KeyValue k="Step" v={`${p.stage_index + 1} / ${p.stage_count}`} />
              <KeyValue k="Provider" v={p.provider ?? "—"} />
              <KeyValue k="Model" v={p.model ?? "—"} />
              <KeyValue k="Model attempt" v={p.model_attempt_number} />
              <KeyValue k="Provider calls" v={p.provider_call_number} />
              <KeyValue k="Recovery actions" v={p.recovery_attempts} />
            </div>
            {p.message && (
              <p className="muted" style={{ marginTop: 14 }}>
                {p.message}
              </p>
            )}
          </>
        ) : (
          <p className="muted">Queued — waiting for the run to start…</p>
        )}
      </section>
    </>
  );
}

function FailureView({ run }: { run: NonNullable<ReturnType<typeof useRun>["run"]> }) {
  // Keep the primary message concise. Backend errors can be very large raw
  // provider payloads (e.g. a Gemini RESOURCE_EXHAUSTED JSON blob); dumping the
  // whole thing into the alert is unreadable. We show a short summary line and
  // tuck the full backend error into an optional, collapsed details element.
  // This is presentation only - it does not change pipeline or recovery
  // behaviour, and the full text remains available to anyone who expands it.
  const rawError = run.error ?? "The run failed without a specific error message.";
  const summary = summarizeError(rawError, run.failed_stage);
  const showDetails = rawError.trim() !== summary.trim();
  const isPartial = run.status === "partially_completed";
  const completed = run.completed_stages ?? [];
  const [resuming, setResuming] = useState(false);
  const [resumeError, setResumeError] = useState<string | null>(null);

  async function onResume() {
    setResuming(true);
    setResumeError(null);
    try {
      await resumeRun(run.run_id);
      // The polling hook will pick up the run returning to "running".
      window.location.reload();
    } catch (e) {
      setResumeError(e instanceof Error ? e.message : "Resume failed.");
      setResuming(false);
    }
  }

  return (
    <>
      <div className={`alert ${isPartial ? "warn" : "error"}`} role="alert">
        <div className="title">
          {isPartial ? "Run partially completed" : "Run failed"}
          {run.failed_stage ? ` at ${run.failed_stage}` : ""}
        </div>
        <div>{summary}</div>
        {completed.length > 0 && (
          <div className="muted" style={{ marginTop: 8 }}>
            {completed.length} stage(s) completed before the failure:{" "}
            {completed.join(", ")}. Their results are available below.
          </div>
        )}
        {run.recovery_attempts != null && run.recovery_attempts > 0 && (
          <div className="muted" style={{ marginTop: 8 }}>
            {run.recovery_attempts} recovery action(s) were attempted before failing.
          </div>
        )}
        {showDetails && (
          <details style={{ marginTop: 8 }}>
            <summary>Technical details</summary>
            <pre
              style={{
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
                maxHeight: 240,
                overflow: "auto",
                marginTop: 8,
              }}
            >
              {rawError}
            </pre>
          </details>
        )}
      </div>
      {run.resumable && (
        <div style={{ marginBottom: 16 }}>
          <button className="primary" onClick={onResume} disabled={resuming}>
            {resuming ? "Resuming…" : "Resume from last checkpoint"}
          </button>
          {resumeError && (
            <div className="alert error" role="alert" style={{ marginTop: 8 }}>
              {resumeError}
            </div>
          )}
        </div>
      )}
      {isPartial && <PartialDownloads runId={run.run_id} />}
      <p>
        <Link to="/">← Start a new run</Link>
      </p>
    </>
  );
}

// Lists whatever artifacts a partially-completed run produced, so completed
// work is downloadable even though the run did not finish (ADR-040).
function PartialDownloads({ runId }: { runId: string }) {
  const [artifacts, setArtifacts] = useState<ArtifactSchema[] | null>(null);
  useEffect(() => {
    let active = true;
    getArtifacts(runId)
      .then((r) => {
        if (active) setArtifacts(r.artifacts);
      })
      .catch(() => {
        if (active) setArtifacts([]);
      });
    return () => {
      active = false;
    };
  }, [runId]);
  if (!artifacts || artifacts.length === 0) return null;
  return <DownloadBar runId={runId} artifacts={artifacts} />;
}

// Reduce a possibly-huge raw provider error to a short, human line. Falls back
// to a length-capped version of the original when we do not recognise it, so we
// never invent detail and never dump an unbounded blob into the headline.
function summarizeError(rawError: string, failedStage: string | null): string {
  const text = rawError.trim();
  const lower = text.toLowerCase();
  const stageSuffix = failedStage ? ` at ${failedStage}` : "";
  if (lower.includes("resource_exhausted") || lower.includes("quota")) {
    return `All available providers/models failed${stageSuffix}. A provider quota was exhausted or unavailable.`;
  }
  if (lower.includes("no api key found")) {
    // Preserve this actionable message as-is; it is already short and useful.
    return text;
  }
  const firstLine = text.split("\n", 1)[0];
  if (firstLine.length <= 200 && firstLine === text) {
    return text;
  }
  return `${firstLine.slice(0, 200)}${firstLine.length > 200 || firstLine !== text ? "…" : ""}`;
}

type ArtifactLoad =
  | { state: "loading" }
  | { state: "no-json"; artifacts: ArtifactSchema[] }
  | { state: "error"; message: string }
  | { state: "ready"; artifact: DesignArtifact; artifacts: ArtifactSchema[] };

function CompletedView({
  runId,
  summary,
}: {
  runId: string;
  summary: NonNullable<ReturnType<typeof useRun>["run"]>["summary"];
}) {
  const [load, setLoad] = useState<ArtifactLoad>({ state: "loading" });

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    (async () => {
      try {
        const list = await getArtifacts(runId, { signal: controller.signal });
        const json = list.artifacts.find((a) => a.name.toLowerCase().endsWith(".json"));
        if (!json) {
          if (!cancelled)
            setLoad({ state: "no-json", artifacts: list.artifacts });
          return;
        }
        const artifact = await getJsonArtifact(runId, json.name, {
          signal: controller.signal,
        });
        if (!cancelled)
          setLoad({ state: "ready", artifact, artifacts: list.artifacts });
      } catch (err) {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        const message =
          err instanceof ApiError
            ? err.detail
            : err instanceof NetworkError
              ? "Could not reach the backend to load results."
              : "Failed to load results.";
        setLoad({ state: "error", message });
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [runId]);

  return (
    <>
      {summary && (
        <section className="panel">
          <h2>Summary</h2>
          <div className="summary-grid">
            <SummaryCard n={summary.requirements} cap="Requirements" />
            <SummaryCard n={summary.business_rules} cap="Business rules" />
            <SummaryCard n={summary.scenarios} cap="Scenarios" />
            <SummaryCard n={summary.test_cases} cap="Test cases" />
            <SummaryCard n={summary.gaps} cap="Gaps" />
            <SummaryCard
              n={`${summary.coverage_percent.toFixed(1)}%`}
              cap="Coverage"
            />
          </div>
        </section>
      )}

      <DownloadBar runId={runId} artifacts={"artifacts" in load ? load.artifacts : []} />

      {load.state === "loading" && (
        <div className="row">
          <span className="spin" aria-hidden="true" />
          <span className="muted">Loading results…</span>
        </div>
      )}
      {load.state === "error" && (
        <div className="alert error" role="alert">
          <div className="title">Could not load results</div>
          <div>{load.message}</div>
        </div>
      )}
      {load.state === "no-json" && (
        <div className="alert warn">
          <div className="title">No JSON artifact</div>
          <div>
            This run produced no JSON report to render inline. Use the download
            links above.
          </div>
        </div>
      )}
      {load.state === "ready" && (
        <Results artifact={load.artifact} summary={summary} />
      )}
    </>
  );
}

function SummaryCard({ n, cap }: { n: number | string; cap: string }) {
  return (
    <div className="summary-card">
      <div className="num">{n}</div>
      <div className="cap">{cap}</div>
    </div>
  );
}

function DownloadBar({
  runId,
  artifacts,
}: {
  runId: string;
  artifacts: ArtifactSchema[];
}) {
  if (artifacts.length === 0) return null;
  return (
    <section className="panel">
      <h2>Downloads</h2>
      <div className="row">
        {artifacts.map((a) => (
          <a
            key={a.name}
            className="btn secondary"
            href={artifactDownloadUrl(runId, a.name)}
            download
          >
            {a.name}
          </a>
        ))}
      </div>
    </section>
  );
}
