import { useEffect, useRef, useState } from "react";
import { getRun } from "../api/client";
import type { RunStatusResponse } from "../api/types";

const POLL_INTERVAL_MS = 2000;

type LoadState = "loading" | "ready" | "error";

export interface UseRunResult {
  run: RunStatusResponse | null;
  loadState: LoadState;
  errorMessage: string | null;
}

function isTerminal(status: string | undefined): boolean {
  return status === "completed" || status === "failed";
}

/**
 * Polls GET /api/v1/runs/{runId} every ~2s while queued/running and stops on a
 * terminal status. Guarantees:
 *  - no overlapping requests (each poll awaits before scheduling the next);
 *  - the in-flight request is aborted and the timer cleared on unmount or when
 *    runId changes;
 *  - a transient request failure does not kill polling - it retries next tick -
 *    but the error is surfaced so the UI can show a soft warning.
 */
export function useRun(runId: string | null): UseRunResult {
  const [run, setRun] = useState<RunStatusResponse | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Kept in refs so the polling loop reads current values without re-subscribing.
  const cancelledRef = useRef(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!runId) {
      return;
    }
    cancelledRef.current = false;
    const controller = new AbortController();

    const scheduleNext = () => {
      if (cancelledRef.current) return;
      timerRef.current = setTimeout(poll, POLL_INTERVAL_MS);
    };

    const poll = async () => {
      if (cancelledRef.current) return;
      try {
        const next = await getRun(runId, { signal: controller.signal });
        if (cancelledRef.current) return;
        setRun(next);
        setLoadState("ready");
        setErrorMessage(null);
        if (!isTerminal(next.status)) {
          scheduleNext();
        }
      } catch (err) {
        if (cancelledRef.current) return;
        // AbortError means we intentionally cancelled; ignore.
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        // Keep any previously loaded run visible; surface a soft error and
        // keep trying, so a brief backend blip doesn't strand the user.
        setErrorMessage(
          err instanceof Error ? err.message : "Failed to reach the backend.",
        );
        setLoadState((prev) => (prev === "ready" ? "ready" : "error"));
        scheduleNext();
      }
    };

    void poll();

    return () => {
      cancelledRef.current = true;
      controller.abort();
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    };
  }, [runId]);

  return { run, loadState, errorMessage };
}
