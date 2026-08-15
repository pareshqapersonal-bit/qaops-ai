import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderRoute } from "../test/render";
import { RunPage } from "./RunPage";
import type { UseRunResult } from "../hooks/useRun";
import type { RunStatusResponse, ClarificationResponse } from "../api/types";
import { queuedRun } from "../test/fixtures";
import * as client from "../api/client";

const { useRunMock } = vi.hoisted(() => ({ useRunMock: vi.fn() }));
vi.mock("../hooks/useRun", () => ({ useRun: () => useRunMock() }));

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    getClarifications: vi.fn(),
    getArtifacts: vi.fn().mockResolvedValue({ run_id: "run_1", artifacts: [] }),
    getJsonArtifact: vi.fn(),
  };
});

function run(status: string): RunStatusResponse {
  return { ...queuedRun, run_id: "run_1", status: status as RunStatusResponse["status"] };
}

function setRun(result: Partial<UseRunResult>) {
  useRunMock.mockReturnValue({
    run: null,
    loadState: "ready",
    errorMessage: null,
    ...result,
  });
}

const clarBatch: ClarificationResponse = {
  run_id: "run_1",
  iteration: 1,
  status: "clarifying",
  questions: [
    {
      question_id: "Q-001",
      question: "Allow retry?",
      priority: "blocking",
      answer_type: "boolean",
      requirement_id: "REQ-001",
      options: [],
      reason: "",
    },
  ],
  readiness: {
    ready: false,
    requirements_total: 1,
    blocking_unanswered: 1,
    recommended_unanswered: 0,
    optional_unanswered: 0,
    critical_gaps: 0,
    blocking_reasons: [],
  },
};

describe("RunPage clarification branches", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders ClarificationPanel when awaiting_clarification", async () => {
    vi.mocked(client.getClarifications).mockResolvedValue(clarBatch);
    setRun({ run: run("awaiting_clarification") });
    renderRoute(<RunPage />, "/runs/:runId", "/runs/run_1");
    await waitFor(() =>
      expect(screen.getByText(/Requirement Clarification/i)).toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Yes" })).toBeInTheDocument();
  });

  it("renders ReadinessGate when ready_for_test_design", async () => {
    vi.mocked(client.getClarifications).mockResolvedValue({
      ...clarBatch,
      status: "ready_for_test_design",
      questions: [],
      readiness: { ...clarBatch.readiness, ready: true, blocking_unanswered: 0 },
    });
    setRun({ run: run("ready_for_test_design") });
    renderRoute(<RunPage />, "/runs/:runId", "/runs/run_1");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /generate test cases/i })).toBeInTheDocument(),
    );
  });

  it("still renders ProgressView for running (no clarification)", () => {
    setRun({ run: run("running") });
    renderRoute(<RunPage />, "/runs/:runId", "/runs/run_1");
    // ProgressView shows the pipeline stages; clarification UI absent.
    expect(screen.queryByText(/Requirement Clarification/i)).not.toBeInTheDocument();
    expect(client.getClarifications).not.toHaveBeenCalled();
  });

  it("shows the clarification status badge label", async () => {
    vi.mocked(client.getClarifications).mockResolvedValue(clarBatch);
    setRun({ run: run("awaiting_clarification") });
    renderRoute(<RunPage />, "/runs/:runId", "/runs/run_1");
    expect(screen.getByText("Awaiting Clarification")).toBeInTheDocument();
  });
});
