import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createDesignRun,
  getClarifications,
  startTestDesign,
  submitClarificationAnswers,
} from "./client";

function mockFetchOnce(body: unknown, init: { ok?: boolean; status?: number } = {}) {
  const ok = init.ok ?? true;
  const status = init.status ?? 200;
  const fetchMock = vi.fn().mockResolvedValue({
    ok,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  } as Response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => vi.unstubAllGlobals());

describe("clarification client", () => {
  it("createDesignRun omits clarify by default", async () => {
    const fetchMock = mockFetchOnce({ run_id: "r1", status: "queued" });
    await createDesignRun(new File(["x"], "r.md"));
    const body = fetchMock.mock.calls[0][1].body as FormData;
    expect(body.get("clarify")).toBeNull();
  });

  it("createDesignRun sends clarify=true when requested", async () => {
    const fetchMock = mockFetchOnce({ run_id: "r1", status: "queued" });
    await createDesignRun(new File(["x"], "r.md"), true);
    const body = fetchMock.mock.calls[0][1].body as FormData;
    expect(body.get("clarify")).toBe("true");
  });

  it("getClarifications GETs the clarifications endpoint", async () => {
    const fetchMock = mockFetchOnce({
      run_id: "r1",
      iteration: 1,
      status: "clarifying",
      questions: [],
      readiness: {
        ready: false,
        requirements_total: 0,
        blocking_unanswered: 0,
        recommended_unanswered: 0,
        optional_unanswered: 0,
        critical_gaps: 0,
        blocking_reasons: [],
      },
    });
    await getClarifications("r1");
    expect(fetchMock.mock.calls[0][0]).toContain("/runs/r1/clarifications");
  });

  it("submitClarificationAnswers POSTs answers as JSON", async () => {
    const fetchMock = mockFetchOnce({
      run_id: "r1",
      iteration: 1,
      status: "clarifying",
      questions: [],
      readiness: {
        ready: true,
        requirements_total: 0,
        blocking_unanswered: 0,
        recommended_unanswered: 0,
        optional_unanswered: 0,
        critical_gaps: 0,
        blocking_reasons: [],
      },
    });
    await submitClarificationAnswers(
      "r1",
      [{ question_id: "Q-001", answer_type: "boolean", answer: "true" }],
      false,
    );
    const call = fetchMock.mock.calls[0][1];
    expect(call.method).toBe("POST");
    const parsed = JSON.parse(call.body as string);
    expect(parsed.answers[0].question_id).toBe("Q-001");
    expect(parsed.proceed_with_assumptions).toBe(false);
  });

  it("startTestDesign POSTs the start-test-design endpoint", async () => {
    const fetchMock = mockFetchOnce({ run_id: "r1", status: "queued" });
    await startTestDesign("r1");
    expect(fetchMock.mock.calls[0][0]).toContain("/runs/r1/start-test-design");
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
  });
});
