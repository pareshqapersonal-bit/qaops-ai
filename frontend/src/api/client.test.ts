import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  NetworkError,
  artifactDownloadUrl,
  createDesignRun,
  getArtifacts,
  getHealth,
  getModels,
  getRun,
} from "./client";
import {
  artifactsList,
  completedRun,
  healthyBackend,
  modelsAvailable,
} from "../test/fixtures";

function mockFetchOnce(body: unknown, init: { ok?: boolean; status?: number } = {}) {
  const ok = init.ok ?? true;
  const status = init.status ?? 200;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok,
      status,
      json: () => Promise.resolve(body),
      text: () => Promise.resolve(JSON.stringify(body)),
    } as Response),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("api client", () => {
  it("parses a health response", async () => {
    mockFetchOnce(healthyBackend);
    await expect(getHealth()).resolves.toEqual(healthyBackend);
  });

  it("parses a models response", async () => {
    mockFetchOnce(modelsAvailable);
    const result = await getModels();
    expect(result.providers).toHaveLength(2);
  });

  it("parses a run status response", async () => {
    mockFetchOnce(completedRun);
    await expect(getRun("run_1")).resolves.toEqual(completedRun);
  });

  it("parses an artifacts response", async () => {
    mockFetchOnce(artifactsList);
    const result = await getArtifacts("run_1");
    expect(result.artifacts.length).toBeGreaterThan(0);
  });

  it("submits a design run with multipart form data", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: () => Promise.resolve({ run_id: "run_9", status: "queued" }),
      text: () => Promise.resolve(""),
    } as Response);
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["hello"], "reqs.md", { type: "text/markdown" });
    const result = await createDesignRun(file);
    expect(result.run_id).toBe("run_9");
    // The request used a FormData body (multipart upload), not JSON.
    const [, init] = fetchMock.mock.calls[0];
    expect(init.body).toBeInstanceOf(FormData);
  });

  it("raises ApiError with the backend detail on a 4xx", async () => {
    mockFetchOnce({ detail: "Unsupported input type .rtf" }, { ok: false, status: 400 });
    await expect(getRun("run_x")).rejects.toBeInstanceOf(ApiError);
    mockFetchOnce({ detail: "Unsupported input type .rtf" }, { ok: false, status: 400 });
    await expect(getRun("run_x")).rejects.toThrow(/Unsupported input/);
  });

  it("raises NetworkError when the backend is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(getHealth()).rejects.toBeInstanceOf(NetworkError);
  });

  it("propagates an AbortError instead of masking it as a network outage", async () => {
    const abort = new DOMException("aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(abort));
    await expect(getHealth()).rejects.toBe(abort);
  });

  it("builds a safe artifact download URL from run id and name", () => {
    const url = artifactDownloadUrl("run_1", "report.json");
    expect(url).toContain("/api/v1/runs/run_1/artifacts/");
    expect(url).toContain("report.json");
  });

  it("encodes artifact names so they cannot break the URL", () => {
    const url = artifactDownloadUrl("run_1", "a b/c.json");
    expect(url).not.toContain(" ");
  });
});
