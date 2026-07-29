import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderRoute } from "../test/render";
import { RunPage } from "./RunPage";
import {

  completedRun,

  failedRun,
  queuedRun,
  runningRun,
} from "../test/fixtures";
import type { UseRunResult } from "../hooks/useRun";

// Mock the polling hook so each test drives a fixed run state; polling itself
// is covered separately in useRun.test.tsx.
const { useRunMock } = vi.hoisted(() => ({ useRunMock: vi.fn() }));
vi.mock("../hooks/useRun", () => ({
  useRun: () => useRunMock(),
}));

// Mock artifact fetches used by the results/artifacts views on completion.
// The fixtures are loaded inside the factory to respect mock hoisting.
vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  const fixtures = await import("../test/fixtures");
  return {
    ...actual,
    getArtifacts: vi.fn().mockResolvedValue(fixtures.artifactsList),
    getJsonArtifact: vi.fn().mockResolvedValue(fixtures.designArtifact),
  };
});

function setRun(result: Partial<UseRunResult>) {
  useRunMock.mockReturnValue({
    run: null,
    loadState: "ready",
    errorMessage: null,
    ...result,
  });
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("RunPage", () => {
  it("shows a loading state before the first response", () => {
    setRun({ run: null, loadState: "loading" });
    renderRoute(<RunPage />, "/runs/:runId", "/runs/run_1");
    expect(screen.getByText(/loading|queued|starting/i)).toBeInTheDocument();
  });

  it("renders the queued state", () => {
    setRun({ run: queuedRun });
    renderRoute(<RunPage />, "/runs/:runId", "/runs/run_1");
    // The status appears in a badge; assert at least one queued indicator shows.
    expect(screen.getAllByText(/queued/i).length).toBeGreaterThan(0);
  });

  it("renders running progress: stage, step, provider, model", () => {
    setRun({ run: runningRun });
    renderRoute(<RunPage />, "/runs/:runId", "/runs/run_1");
    expect(screen.getByText(/gap_analyzer/i)).toBeInTheDocument();
    expect(screen.getByText("3 / 6")).toBeInTheDocument();
    expect(screen.getByText(/openrouter/i)).toBeInTheDocument();
    expect(screen.getByText(/cohere\/north-mini-code:free/i)).toBeInTheDocument();
  });

  it("shows provider call count and recovery actions", () => {
    setRun({ run: runningRun });
    renderRoute(<RunPage />, "/runs/:runId", "/runs/run_1");
    // provider_call_number = 5, recovery_attempts = 4 in the fixture. Scope to
    // the labeled key/value rows so we don't collide with other "5"s.
    const callsRow = screen.getByText("Provider calls").parentElement;
    expect(callsRow?.textContent).toContain("5");
    const recoveryRow = screen.getByText("Recovery actions").parentElement;
    expect(recoveryRow?.textContent).toContain("4");
  });

  it("shows a human-readable status message, not raw field names", () => {
    setRun({ run: runningRun });
    renderRoute(<RunPage />, "/runs/:runId", "/runs/run_1");
    expect(screen.getByText(/waiting for provider response/i)).toBeInTheDocument();
    expect(screen.queryByText("provider_call_number")).not.toBeInTheDocument();
  });

  it("renders the completed summary with backend values", () => {
    setRun({ run: completedRun });
    renderRoute(<RunPage />, "/runs/:runId", "/runs/run_1");
    // Summary values come straight from the fixture's summary object. Scope to
    // labeled cells to avoid collisions with unrelated numbers.
    const s = completedRun.summary!;
    const reqRow = screen.getByText(/^requirements$/i).parentElement;
    expect(reqRow?.textContent).toContain(String(s.requirements));
    const tcRow = screen.getByText(/^test cases$/i).parentElement;
    expect(tcRow?.textContent).toContain(String(s.test_cases));
  });

  it("presents a concise failure with the failed stage", () => {
    setRun({ run: failedRun });
    renderRoute(<RunPage />, "/runs/:runId", "/runs/run_1");
    // The failure alert names the stage it failed at.
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toMatch(/run failed/i);
    expect(alert.textContent).toContain("requirement_analyzer");
  });

  it("shows the backend error text and recovery-action count on failure", () => {
    setRun({ run: failedRun });
    renderRoute(<RunPage />, "/runs/:runId", "/runs/run_1");
    const alert = screen.getByRole("alert");
    // The backend error is shown (already secret-redacted server-side).
    expect(alert.textContent).toContain(failedRun.error!.slice(0, 20));
    // recovery_attempts = 12 in the fixture.
    expect(alert.textContent).toMatch(/12 recovery action/i);
  });

  it("shows a soft warning when a poll fails but keeps the run visible", () => {
    setRun({ run: runningRun, errorMessage: "Backend hiccup" });
    renderRoute(<RunPage />, "/runs/:runId", "/runs/run_1");
    // The running progress is still shown...
    expect(screen.getByText("gap_analyzer")).toBeInTheDocument();
    // ...alongside a non-fatal reconnecting notice carrying the message.
    const statuses = screen.getAllByRole("status");
    const reconnect = statuses.find((el) => /reconnect/i.test(el.textContent ?? ""));
    expect(reconnect).toBeDefined();
    expect(reconnect?.textContent).toContain("Backend hiccup");
  });

  it("renders a run whose error was already redacted by the backend", () => {
    // The backend redacts secrets before they reach the API (Phase 16.2). The
    // UI shows the provided text verbatim; it must not reconstruct or expose
    // anything beyond what the backend sent. Here the backend already redacted.
    const redacted = {
      ...failedRun,
      error: "auth failed for key [redacted] on provider openrouter",
    };
    setRun({ run: redacted });
    renderRoute(<RunPage />, "/runs/:runId", "/runs/run_1");
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("[redacted]");
    expect(alert.textContent).not.toMatch(/sk-[a-z0-9]/i);
  });

  it("summarizes a huge provider payload concisely and hides raw text in details", () => {
    // A Gemini RESOURCE_EXHAUSTED-style blob: large and JSON-ish.
    const hugePayload =
      '{"error":{"code":429,"status":"RESOURCE_EXHAUSTED","message":"' +
      "Quota exceeded for quota metric 'Generate requests'. ".repeat(40) +
      '"}}';
    const failed = {
      ...failedRun,
      error: hugePayload,
      failed_stage: "requirement_analyzer",
    };
    setRun({ run: failed });
    renderRoute(<RunPage />, "/runs/:runId", "/runs/run_1");
    const alert = screen.getByRole("alert");
    // The concise summary is shown, not the whole blob, in the headline area.
    expect(alert.textContent).toMatch(/provider quota was exhausted or unavailable/i);
    // The raw payload is available but tucked into a collapsible details block.
    const details = alert.querySelector("details");
    expect(details).not.toBeNull();
    expect(details?.textContent).toContain("RESOURCE_EXHAUSTED");
    // The stage is still named.
    expect(alert.textContent).toContain("requirement_analyzer");
  });

  it("shows a short error inline without a details disclosure", () => {
    const failed = { ...failedRun, error: "All providers failed for this stage." };
    setRun({ run: failed });
    renderRoute(<RunPage />, "/runs/:runId", "/runs/run_1");
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("All providers failed for this stage.");
    // No details element for an already-concise message.
    expect(alert.querySelector("details")).toBeNull();
  });
});
