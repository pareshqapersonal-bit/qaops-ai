import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ReadinessGate } from "./ReadinessGate";
import type { ClarificationResponse } from "../api/types";
import * as client from "../api/client";
import { ApiError } from "../api/client";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, startTestDesign: vi.fn() };
});

function readyResponse(): ClarificationResponse {
  return {
    run_id: "run_1",
    iteration: 2,
    status: "ready_for_test_design",
    questions: [],
    readiness: {
      ready: true,
      requirements_total: 5,
      blocking_unanswered: 0,
      recommended_unanswered: 2,
      optional_unanswered: 0,
      critical_gaps: 0,
      blocking_reasons: [],
    },
  };
}

describe("ReadinessGate", () => {
  beforeEach(() => vi.clearAllMocks());

  it("shows readiness summary and the Generate Test Cases CTA", () => {
    render(<ReadinessGate runId="run_1" data={readyResponse()} />);
    expect(screen.getByText(/5 requirements understood/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /generate test cases/i })).toBeInTheDocument();
  });

  it("calls startTestDesign on CTA click", async () => {
    vi.mocked(client.startTestDesign).mockResolvedValue({ run_id: "run_1", status: "queued" });
    render(<ReadinessGate runId="run_1" data={readyResponse()} />);
    fireEvent.click(screen.getByRole("button", { name: /generate test cases/i }));
    await waitFor(() => expect(client.startTestDesign).toHaveBeenCalledWith("run_1"));
  });

  it("shows backend detail on 409", async () => {
    vi.mocked(client.startTestDesign).mockRejectedValue(new ApiError(409, "Run is running."));
    render(<ReadinessGate runId="run_1" data={readyResponse()} />);
    fireEvent.click(screen.getByRole("button", { name: /generate test cases/i }));
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toContain("Run is running."),
    );
  });
});
