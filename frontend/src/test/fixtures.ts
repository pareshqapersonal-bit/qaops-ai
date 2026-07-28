// Fixtures for every backend state the UI must handle (spec section 28).
// Shapes match the real OpenAPI schema exactly, so tests exercise the true
// contract. No live provider calls ever occur.

import type {
  ArtifactsResponse,
  DesignArtifact,
  HealthResponse,
  ModelsResponse,
  RunStatusResponse,
} from "../api/types";
import artifactJson from "./artifact.fixture.json";

export const healthyBackend: HealthResponse = {
  status: "ok",
  service: "qaops-ai",
  version: "0.18.0.dev0",
};

export const modelsAvailable: ModelsResponse = {
  providers: [
    {
      provider: "openrouter",
      source: "static",
      models: [
        {
          id: "deepseek/deepseek-chat",
          max_context_tokens: 64000,
          max_output_tokens: 8192,
          structured_output: true,
          local: false,
          free: false,
        },
      ],
    },
    {
      provider: "gemini",
      source: "static",
      models: [
        {
          id: "gemini-2.5-flash",
          max_context_tokens: 1000000,
          max_output_tokens: 8192,
          structured_output: true,
          local: false,
          free: false,
        },
      ],
    },
  ],
};

export const noProviders: ModelsResponse = { providers: [] };

export const queuedRun: RunStatusResponse = {
  run_id: "run_abc123",
  status: "queued",
  entry_point: null,
  detection: null,
  summary: null,
  progress: null,
  error: null,
  failed_stage: null,
  recovery_attempts: null,
};

export const runningRun: RunStatusResponse = {
  run_id: "run_abc123",
  status: "running",
  entry_point: "document",
  detection: "requirement document (PDF)",
  summary: null,
  progress: {
    current_stage: "gap_analyzer",
    stage_index: 2,
    stage_count: 6,
    provider: "openrouter",
    model: "cohere/north-mini-code:free",
    model_attempt_number: 1,
    request_attempt: 2,
    provider_call_number: 5,
    models_attempted: 0,
    recovery_attempts: 4,
    message: "Waiting for provider response",
  },
  error: null,
  failed_stage: null,
  recovery_attempts: 4,
};

export const completedRun: RunStatusResponse = {
  run_id: "run_abc123",
  status: "completed",
  entry_point: "document",
  detection: "requirement document (PDF)",
  summary: {
    requirements: 38,
    business_rules: 38,
    scenarios: 23,
    test_cases: 14,
    gaps: 44,
    coverage_percent: 60.9,
  },
  progress: {
    current_stage: "coverage_validator",
    stage_index: 5,
    stage_count: 6,
    provider: "openrouter",
    model: "deepseek/deepseek-chat",
    model_attempt_number: 1,
    request_attempt: 1,
    provider_call_number: 6,
    models_attempted: 0,
    recovery_attempts: 0,
    message: "Done.",
  },
  error: null,
  failed_stage: null,
  recovery_attempts: 0,
};

export const failedRun: RunStatusResponse = {
  run_id: "run_abc123",
  status: "failed",
  entry_point: "document",
  detection: "requirement document (PDF)",
  summary: null,
  progress: {
    current_stage: "requirement_analyzer",
    stage_index: 0,
    stage_count: 6,
    provider: "openrouter",
    model: "cohere/north-mini-code:free",
    model_attempt_number: 5,
    request_attempt: 1,
    provider_call_number: 12,
    models_attempted: 0,
    recovery_attempts: 12,
    message: "stage recovery budget exhausted",
  },
  error:
    "[requirement_analyzer] All providers failed. Last error from openrouter/cohere/north-mini-code:free: model returned no content.",
  failed_stage: "requirement_analyzer",
  recovery_attempts: 12,
};

export const artifactsList: ArtifactsResponse = {
  run_id: "run_abc123",
  artifacts: [
    { name: "reqs.json", format: "json" },
    { name: "reqs.md", format: "markdown" },
  ],
};

// The real JSON artifact captured from a genuine pipeline run.
export const designArtifact = artifactJson as unknown as DesignArtifact;
