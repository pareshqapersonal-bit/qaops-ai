import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useClarification } from "./useClarification";
import * as client from "../api/client";
import { ApiError } from "../api/client";
import type { ClarificationResponse } from "../api/types";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    getClarifications: vi.fn(),
    submitClarificationAnswers: vi.fn(),
  };
});

function batch(overrides: Partial<ClarificationResponse> = {}): ClarificationResponse {
  return {
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
    ...overrides,
  };
}

describe("useClarification", () => {
  beforeEach(() => vi.clearAllMocks());

  it("loads the current batch", async () => {
    vi.mocked(client.getClarifications).mockResolvedValue(batch());
    const { result } = renderHook(() => useClarification("run_1", true));
    await waitFor(() => expect(result.current.loadState).toBe("ready"));
    expect(result.current.data?.questions).toHaveLength(1);
  });

  it("does not load when disabled", async () => {
    renderHook(() => useClarification("run_1", false));
    expect(client.getClarifications).not.toHaveBeenCalled();
  });

  it("collects and submits answers as one batch", async () => {
    vi.mocked(client.getClarifications).mockResolvedValue(batch());
    const ready = batch({
      readiness: { ...batch().readiness, ready: true, blocking_unanswered: 0 },
    });
    vi.mocked(client.submitClarificationAnswers).mockResolvedValue(ready);
    const { result } = renderHook(() => useClarification("run_1", true));
    await waitFor(() => expect(result.current.data).not.toBeNull());

    act(() => result.current.setAnswer("Q-001", "true"));
    await act(async () => {
      await result.current.submit(false);
    });

    expect(client.submitClarificationAnswers).toHaveBeenCalledWith(
      "run_1",
      [{ question_id: "Q-001", answer_type: "boolean", answer: "true" }],
      false,
    );
    // The response updates the displayed batch/readiness.
    expect(result.current.data?.readiness.ready).toBe(true);
  });

  it("sets movedOn on a 409 during load", async () => {
    vi.mocked(client.getClarifications).mockRejectedValue(new ApiError(409, "moved on"));
    const { result } = renderHook(() => useClarification("run_1", true));
    await waitFor(() => expect(result.current.movedOn).toBe(true));
  });

  it("surfaces the backend detail on a 400 submit", async () => {
    vi.mocked(client.getClarifications).mockResolvedValue(batch());
    vi.mocked(client.submitClarificationAnswers).mockRejectedValue(
      new ApiError(400, "Contradictory answers for Q-001"),
    );
    const { result } = renderHook(() => useClarification("run_1", true));
    await waitFor(() => expect(result.current.data).not.toBeNull());
    act(() => result.current.setAnswer("Q-001", "true"));
    await act(async () => {
      await result.current.submit(false);
    });
    expect(result.current.submitError).toContain("Contradictory answers");
  });

  it("passes proceedWithAssumptions through", async () => {
    vi.mocked(client.getClarifications).mockResolvedValue(batch());
    vi.mocked(client.submitClarificationAnswers).mockResolvedValue(batch());
    const { result } = renderHook(() => useClarification("run_1", true));
    await waitFor(() => expect(result.current.data).not.toBeNull());
    await act(async () => {
      await result.current.submit(true);
    });
    expect(client.submitClarificationAnswers).toHaveBeenCalledWith("run_1", [], true);
  });
});
