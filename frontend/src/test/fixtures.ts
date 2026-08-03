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
    test_conditions: 31,
    test_cases: 14,
    gaps: 44,
    coverage_percent: 60.9,
    requirement_coverage_percent: 60.9,
    business_rule_coverage_percent: 55.0,
    scenario_coverage_percent: 70.0,
    condition_coverage_percent: 48.4,
    unresolved_conditions: 3,
    expansion_truncated: false,
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

export const partiallyCompletedRun: RunStatusResponse = {
  run_id: "run_partial",
  status: "partially_completed",
  entry_point: "document",
  detection: "requirement document (DOCX)",
  summary: null,
  progress: {
    current_stage: "test_condition_analyzer",
    stage_index: 4,
    stage_count: 7,
    provider: "openrouter",
    model: "some/model:free",
    model_attempt_number: 3,
    request_attempt: 1,
    provider_call_number: 6,
    models_attempted: 0,
    recovery_attempts: 3,
    message: "stage recovery budget exhausted",
  },
  error: "[test_condition_analyzer] All providers failed.",
  failed_stage: "test_condition_analyzer",
  recovery_attempts: 3,
  resumable: true,
  completed_stages: [
    "requirement_analyzer",
    "business_rule_extractor",
    "gap_analyzer",
    "scenario_generator",
  ],
  stage_statuses: [
    { stage: "requirement_analyzer", status: "completed" },
    { stage: "scenario_generator", status: "completed" },
    { stage: "test_condition_analyzer", status: "failed" },
  ],
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
