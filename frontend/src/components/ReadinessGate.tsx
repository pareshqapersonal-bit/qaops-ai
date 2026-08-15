import { useState } from "react";
import { ApiError, startTestDesign } from "../api/client";
import type { ClarificationResponse } from "../api/types";
import { KeyValue, Panel } from "./common";

interface Props {
  runId: string;
  data: ClarificationResponse;
}

/**
 * Shown when a run is READY_FOR_TEST_DESIGN. Summarizes readiness and offers the
 * Generate Test Cases CTA, which hands off to the existing pipeline. On success
 * the run moves to queued/running and the existing useRun polling in RunPage
 * returns the user to ProgressView - this component creates no new result page.
 */
export function ReadinessGate({ runId, data }: Props) {
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const resolved =
    data.readiness.requirements_total > 0
      ? `${data.readiness.requirements_total} requirement${
          data.readiness.requirements_total === 1 ? "" : "s"
        } understood`
      : "Requirements understood";

  const onGenerate = async () => {
    setStarting(true);
    setError(null);
    try {
      await startTestDesign(runId);
      // Do not navigate; useRun polling in RunPage will pick up queued/running
      // and render ProgressView.
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.detail);
      } else {
        setError(err instanceof Error ? err.message : "Failed to start test design.");
      }
      setStarting(false);
    }
  };

  return (
    <Panel title="Ready for Test Design">
      <div className="clar-ready">
        <div className="clar-check-line">✓ {resolved}</div>
        <div className="clar-check-line">
          ✓ {data.readiness.blocking_unanswered} blocking question
          {data.readiness.blocking_unanswered === 1 ? "" : "s"} unanswered
        </div>
        <div className="clar-check-line">✓ {data.readiness.critical_gaps} critical gaps</div>
      </div>

      <div className="row" style={{ gap: 16, marginTop: 12 }}>
        <KeyValue k="Recommended open" v={data.readiness.recommended_unanswered} />
        <KeyValue k="Optional open" v={data.readiness.optional_unanswered} />
      </div>

      {error && (
        <div className="alert error" role="alert" style={{ marginTop: 12 }}>
          {error}
        </div>
      )}

      <div style={{ marginTop: 16 }}>
        <button className="btn" onClick={onGenerate} disabled={starting}>
          {starting ? "Starting…" : "Generate Test Cases"}
        </button>
      </div>
    </Panel>
  );
}
