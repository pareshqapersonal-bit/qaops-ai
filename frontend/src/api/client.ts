// A small typed API layer over the QAOps FastAPI backend.
// Centralizes the base URL, HTTP error handling, JSON parsing, and request
// cancellation so components never touch fetch directly (ADR-032).

import type {
  ArtifactsResponse,
  DesignArtifact,
  HealthResponse,
  ModelsResponse,
  RunCreatedResponse,
  RunStatusResponse,
} from "./types";

// Base URL for API requests.
// - Production (served by FastAPI): defaults to "" so requests are same-origin
//   and relative (e.g. fetch("/api/v1/models") hits the serving host). No
//   hard-coded backend host ends up in the production bundle.
// - Development: set VITE_API_BASE_URL=http://localhost:8000 (or 127.0.0.1) so
//   the Vite dev server on :5173 can reach FastAPI on :8000.
// The nullish check means an explicitly empty value is respected; only an
// undefined VITE_API_BASE_URL falls through to same-origin.
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "";

// A structured error every caller can inspect, instead of a bare string.
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail || `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

// Raised when the backend cannot be reached at all (offline, DNS, CORS).
export class NetworkError extends Error {
  constructor(message = "The QAOps backend is unavailable.") {
    super(message);
    this.name = "NetworkError";
  }
}

interface RequestOptions {
  signal?: AbortSignal;
}

async function request<T>(
  path: string,
  options: RequestInit & RequestOptions = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, options);
  } catch (err) {
    // AbortError is a legitimate cancellation, not a backend outage.
    if (err instanceof DOMException && err.name === "AbortError") {
      throw err;
    }
    throw new NetworkError();
  }

  if (!response.ok) {
    // FastAPI error bodies look like { "detail": "..." }. Parse defensively.
    let detail = `Request failed with status ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: unknown };
      if (typeof body.detail === "string") {
        detail = body.detail;
      }
    } catch {
      // Non-JSON error body; keep the default message.
    }
    throw new ApiError(response.status, detail);
  }

  // 204 or empty bodies would break json(); guard for completeness.
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function getHealth(opts?: RequestOptions): Promise<HealthResponse> {
  return request<HealthResponse>("/health", { signal: opts?.signal });
}

export function getModels(opts?: RequestOptions): Promise<ModelsResponse> {
  return request<ModelsResponse>("/api/v1/models", { signal: opts?.signal });
}

export function createDesignRun(
  file: File,
  opts?: RequestOptions,
): Promise<RunCreatedResponse> {
  const form = new FormData();
  // The backend multipart field is named "file" (verified against the API).
  form.append("file", file);
  return request<RunCreatedResponse>("/api/v1/design", {
    method: "POST",
    body: form,
    signal: opts?.signal,
  });
}

// A Jira-style ticket accepted by POST /api/v1/design/ticket (Phase 32/35). The
// ticket is normalized to Markdown server-side; optional design/reference
// attachments are extracted and appended as evidence (one section per file, in
// order), then the combined document flows through the same document pipeline.
// Sent as multipart FormData. The multipart field name is "attachment" (repeated
// once per file), matching the backend list[UploadFile] param (Phase 35B).
export interface TicketInput {
  title: string;
  description: string;
  ticket_id?: string;
  priority?: string;
  labels?: string[];
}

export function createTicketRun(
  ticket: TicketInput,
  attachments?: File[] | null,
  opts?: RequestOptions,
): Promise<RunCreatedResponse> {
  const form = new FormData();
  form.append("title", ticket.title);
  form.append("description", ticket.description);
  if (ticket.ticket_id) form.append("ticket_id", ticket.ticket_id);
  if (ticket.priority) form.append("priority", ticket.priority);
  // List fields are sent as repeated keys, matching the backend list[str] Form param.
  for (const label of ticket.labels ?? []) form.append("labels", label);
  // Each file is appended under the same "attachment" field name, in order.
  for (const file of attachments ?? []) form.append("attachment", file);
  return request<RunCreatedResponse>("/api/v1/design/ticket", {
    method: "POST",
    body: form,
    signal: opts?.signal,
  });
}

export function getRun(
  runId: string,
  opts?: RequestOptions,
): Promise<RunStatusResponse> {
  return request<RunStatusResponse>(
    `/api/v1/runs/${encodeURIComponent(runId)}`,
    { signal: opts?.signal },
  );
}

export function getArtifacts(
  runId: string,
  opts?: RequestOptions,
): Promise<ArtifactsResponse> {
  return request<ArtifactsResponse>(
    `/api/v1/runs/${encodeURIComponent(runId)}/artifacts`,
    { signal: opts?.signal },
  );
}

// Resume a resumable run from its last checkpoint (ADR-040). Completed stages
// are reused; only the remaining stages run.
export function resumeRun(
  runId: string,
  opts?: RequestOptions,
): Promise<RunCreatedResponse> {
  return request<RunCreatedResponse>(
    `/api/v1/runs/${encodeURIComponent(runId)}/resume`,
    { method: "POST", signal: opts?.signal },
  );
}

// Request cooperative cancellation; the run stops at the next stage boundary.
export function cancelRun(
  runId: string,
  opts?: RequestOptions,
): Promise<RunStatusResponse> {
  return request<RunStatusResponse>(
    `/api/v1/runs/${encodeURIComponent(runId)}/cancel`,
    { method: "POST", signal: opts?.signal },
  );
}

// A plain URL for a browser download / anchor href. The backend enforces
// path safety and authorization; the client only builds the address.
export function artifactDownloadUrl(runId: string, name: string): string {
  return `${API_BASE_URL}/api/v1/runs/${encodeURIComponent(
    runId,
  )}/artifacts/${encodeURIComponent(name)}`;
}

// Fetch and parse the JSON artifact that backs the results views.
export async function getJsonArtifact(
  runId: string,
  name: string,
  opts?: RequestOptions,
): Promise<DesignArtifact> {
  return request<DesignArtifact>(
    `/api/v1/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(name)}`,
    { signal: opts?.signal },
  );
}
