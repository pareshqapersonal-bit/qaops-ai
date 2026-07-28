import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render } from "@testing-library/react";
import { useRun } from "./useRun";
import { completedRun, failedRun, queuedRun, runningRun } from "../test/fixtures";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, getRun: vi.fn() };
});
const { getRun } = await import("../api/client");
const getRunMock = getRun as ReturnType<typeof vi.fn>;

// A tiny probe component that surfaces the hook's output for assertions.
function Probe({ runId }: { runId: string | null }) {
  const { run, loadState, errorMessage } = useRun(runId);
  return (
    <div>
      <span data-testid="status">{run?.status ?? "none"}</span>
      <span data-testid="loadState">{loadState}</span>
      <span data-testid="error">{errorMessage ?? ""}</span>
    </div>
  );
}

beforeEach(() => {
  vi.useFakeTimers();
  getRunMock.mockReset();
});

afterEach(() => {
  vi.runOnlyPendingTimers();
  vi.useRealTimers();
});

describe("useRun polling", () => {
  it("loads the initial run state", async () => {
    getRunMock.mockResolvedValue(queuedRun);
    const { getByTestId } = render(<Probe runId="run_1" />);
    await vi.waitFor(() => expect(getByTestId("status").textContent).toBe("queued"));
  });

  it("keeps polling while running and stops on completion", async () => {
    getRunMock
      .mockResolvedValueOnce(runningRun)
      .mockResolvedValueOnce(runningRun)
      .mockResolvedValueOnce(completedRun);
    const { getByTestId } = render(<Probe runId="run_1" />);

    await vi.waitFor(() => expect(getByTestId("status").textContent).toBe("running"));
    // Advance two poll intervals; the third response completes the run.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await vi.waitFor(() => expect(getByTestId("status").textContent).toBe("completed"));

    const callsAtCompletion = getRunMock.mock.calls.length;
    // No further polling after a terminal status.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(getRunMock.mock.calls.length).toBe(callsAtCompletion);
  });

  it("stops polling on failure", async () => {
    getRunMock.mockResolvedValue(failedRun);
    const { getByTestId } = render(<Probe runId="run_1" />);
    await vi.waitFor(() => expect(getByTestId("status").textContent).toBe("failed"));
    const calls = getRunMock.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    expect(getRunMock.mock.calls.length).toBe(calls);
  });

  it("does not overlap requests: one poll resolves before the next is scheduled", async () => {
    let resolveFirst: (v: unknown) => void = () => {};
    getRunMock.mockReturnValueOnce(
      new Promise((r) => {
        resolveFirst = r;
      }),
    );
    render(<Probe runId="run_1" />);
    // While the first request is in flight, advancing time must not fire a
    // second call (the next is only scheduled after the first resolves).
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(getRunMock).toHaveBeenCalledTimes(1);
    resolveFirst(runningRun);
  });

  it("surfaces a transient error but keeps the previous run and retries", async () => {
    getRunMock
      .mockResolvedValueOnce(runningRun)
      .mockRejectedValueOnce(new Error("Backend hiccup"))
      .mockResolvedValueOnce(completedRun);
    const { getByTestId } = render(<Probe runId="run_1" />);
    await vi.waitFor(() => expect(getByTestId("status").textContent).toBe("running"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    // Error surfaced, but the last known status is retained (still "running").
    await vi.waitFor(() => expect(getByTestId("error").textContent).toMatch(/hiccup/i));
    expect(getByTestId("status").textContent).toBe("running");
    // Next tick recovers to completed.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await vi.waitFor(() => expect(getByTestId("status").textContent).toBe("completed"));
  });

  it("cleans up on unmount: no polling after the component is gone", async () => {
    getRunMock.mockResolvedValue(runningRun);
    const { unmount, getByTestId } = render(<Probe runId="run_1" />);
    await vi.waitFor(() => expect(getByTestId("status").textContent).toBe("running"));
    const calls = getRunMock.mock.calls.length;
    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(getRunMock.mock.calls.length).toBe(calls);
  });
});
