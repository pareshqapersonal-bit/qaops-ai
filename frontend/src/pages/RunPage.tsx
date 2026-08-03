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
import type { ArtifactSchema, DesignArtifact } from "../api/types";
import { useRun } from "../hooks/useRun";
import {
  PIPELINE_STAGES,
  deriveStageStates,
} from "../hooks/useBackendStatus";
import { KeyValue, StatusBadge } from "../components/common";
import { Results } from "../components/Results";

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

      {run.status === "failed" || run.status === "partially_completed" || run.status === "cancelled" ? (
        <FailureView run={run} />
      ) : run.status === "completed" ? (
        <CompletedView runId={run.run_id} summary={run.summary} />
      ) : (
        <ProgressView run={run} />
      )}
    </div>
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
