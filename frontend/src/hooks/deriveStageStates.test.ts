import { describe, it, expect } from "vitest";
import { deriveStageStates, PIPELINE_STAGES } from "./useBackendStatus";
import type { RunStatusResponse } from "../api/types";

// Minimal run builder for the fields deriveStageStates reads.
function run(partial: Partial<RunStatusResponse>): RunStatusResponse {
  return {
    run_id: "r",
    status: "running",
    entry_point: null,
    detection: null,
    summary: null,
    progress: null,
    error: null,
    failed_stage: null,
    recovery_attempts: null,
    ...partial,
  } as RunStatusResponse;
}

describe("deriveStageStates", () => {
  // Regression guard: the frontend progress journey must list exactly the
  // stages the backend executes for the DOCUMENT entry point, in order. This
  // mirrors stage_names_for(EntryPoint.DOCUMENT); a matching backend test
  // (test_stage_order.py) exports the same canonical list so drift on either
  // side is caught.
  it("matches the backend DOCUMENT pipeline stage order exactly", () => {
    expect(PIPELINE_STAGES.map((s) => s.key)).toEqual([
      "requirement_analyzer",
      "business_rule_extractor",
      "gap_analyzer",
      "scenario_generator",
      "test_condition_analyzer",
      "test_case_generator",
      "coverage_validator",
    ]);
  });

  it("has seven stages (test_condition_analyzer is not omitted)", () => {
    expect(PIPELINE_STAGES).toHaveLength(7);
    expect(PIPELINE_STAGES.map((s) => s.key)).toContain("test_condition_analyzer");
  });

  it("marks all pending for a queued run with no progress", () => {
    expect(deriveStageStates(run({ status: "queued" }))).toEqual(
      Array(PIPELINE_STAGES.length).fill("pending"),
    );
  });

  it("reconstructs completed stages on resume before the active stage arrives", () => {
    // A resumed run: the backend has carried over stage_statuses from the first
    // attempt (requirement_analyzer..scenario_generator completed), but the
    // resumed stage's STAGE_STARTED event has not yet set current_stage.
    const states = deriveStageStates(
      run({
        status: "running",
        progress: null, // current_stage not yet known
        completed_stages: [
          "requirement_analyzer",
          "business_rule_extractor",
          "gap_analyzer",
          "scenario_generator",
        ],
        stage_statuses: [
          { stage: "requirement_analyzer", status: "completed" },
          { stage: "business_rule_extractor", status: "completed" },
          { stage: "gap_analyzer", status: "completed" },
          { stage: "scenario_generator", status: "completed" },
        ],
      }),
    );
    // The four completed stages must show completed, not pending.
    const byKey = Object.fromEntries(
      PIPELINE_STAGES.map((s, i) => [s.key, states[i]]),
    );
    expect(byKey["requirement_analyzer"]).toBe("completed");
    expect(byKey["business_rule_extractor"]).toBe("completed");
    expect(byKey["gap_analyzer"]).toBe("completed");
    expect(byKey["scenario_generator"]).toBe("completed");
  });

  it("overlays the active stage on top of reconstructed completed stages", () => {
    const states = deriveStageStates(
      run({
        status: "running",
        progress: {
          current_stage: "coverage_validator",
          stage_index: 5,
          stage_count: 6,
          provider: "mock",
          model: "m",
          model_attempt_number: 1,
          request_attempt: 1,
          provider_call_number: 1,
          models_attempted: 0,
          recovery_attempts: 0,
          message: "",
        },
        completed_stages: ["requirement_analyzer"],
        stage_statuses: [{ stage: "requirement_analyzer", status: "completed" }],
      }),
    );
    const byKey = Object.fromEntries(
      PIPELINE_STAGES.map((s, i) => [s.key, states[i]]),
    );
    expect(byKey["requirement_analyzer"]).toBe("completed");
    expect(byKey["coverage_validator"]).toBe("active");
  });

  it("still marks all completed for a completed run", () => {
    expect(deriveStageStates(run({ status: "completed" }))).toEqual(
      Array(PIPELINE_STAGES.length).fill("completed"),
    );
  });
});
