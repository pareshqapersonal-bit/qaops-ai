import { useEffect, useState } from "react";
import { getHealth, getModels } from "../api/client";
import type { RunStatusResponse } from "../api/types";

export type BackendState = "checking" | "online" | "offline";

export interface BackendStatus {
  state: BackendState;
  version: string | null;
  providerCount: number | null;
}

/**
 * Checks GET /health and GET /api/v1/models once on mount. Provider count is
 * informational only (spec section 7); a models failure does not make the
 * backend "offline" as long as /health responds.
 */
export function useBackendStatus(): BackendStatus {
  const [status, setStatus] = useState<BackendStatus>({
    state: "checking",
    version: null,
    providerCount: null,
  });

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    (async () => {
      try {
        const health = await getHealth({ signal: controller.signal });
        if (cancelled) return;
        let providerCount: number | null = null;
        try {
          const models = await getModels({ signal: controller.signal });
          providerCount = models.providers.length;
        } catch {
          // Providers are informational; ignore a models failure.
        }
        if (cancelled) return;
        setStatus({ state: "online", version: health.version, providerCount });
      } catch (err) {
        if (cancelled) return;
        if (err instanceof DOMException && err.name === "AbortError") return;
        setStatus({ state: "offline", version: null, providerCount: null });
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, []);

  return status;
}

// --- pipeline stage derivation ----------------------------------------------

// The six pipeline stages, in order, with human labels. The internal stage
// names come from the backend progress.current_stage field.
export const PIPELINE_STAGES: { key: string; label: string }[] = [
  { key: "requirement_analyzer", label: "Requirement Analysis" },
  { key: "business_rule_extractor", label: "Business Rules" },
  { key: "gap_analyzer", label: "Gap Analysis" },
  { key: "scenario_generator", label: "Scenario Generation" },
  { key: "test_case_generator", label: "Test Case Generation" },
  { key: "coverage_validator", label: "Coverage Validation" },
];

export type StageState = "pending" | "active" | "completed" | "failed";

/**
 * Derive each stage's visual state conservatively from run status and progress.
 * We never fabricate percentages. Rules:
 *  - completed run  -> all stages completed;
 *  - failed run     -> the failed stage is "failed", earlier stages completed,
 *                      later stages pending;
 *  - running/queued -> stages before the current index are completed, the
 *                      current is active, the rest pending. If we have no
 *                      progress yet (queued), all are pending.
 */
export function deriveStageStates(
  run: Pick<RunStatusResponse, "status" | "progress" | "failed_stage"> | null,
): StageState[] {
  const n = PIPELINE_STAGES.length;
  if (!run) return Array(n).fill("pending");

  if (run.status === "completed") {
    return Array(n).fill("completed");
  }

  const currentKey = run.progress?.current_stage ?? null;
  const currentIndex = currentKey
    ? PIPELINE_STAGES.findIndex((s) => s.key === currentKey)
    : -1;

  if (run.status === "failed") {
    const failedKey = run.failed_stage ?? currentKey;
    const failedIndex = failedKey
      ? PIPELINE_STAGES.findIndex((s) => s.key === failedKey)
      : currentIndex;
    return PIPELINE_STAGES.map((_, i) => {
      if (failedIndex >= 0 && i < failedIndex) return "completed";
      if (i === failedIndex) return "failed";
      return "pending";
    });
  }

  // queued or running
  return PIPELINE_STAGES.map((_, i) => {
    if (currentIndex < 0) return "pending";
    if (i < currentIndex) return "completed";
    if (i === currentIndex) return "active";
    return "pending";
  });
}
