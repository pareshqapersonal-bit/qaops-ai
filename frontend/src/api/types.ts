// Types mirroring the QAOps backend OpenAPI schema (v0.18.0-dev).
// These are derived from the actual Pydantic response models - NOT guessed.
// See docs/adr/032-frontend-architecture.md.

// --- API response envelopes -------------------------------------------------

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
}

export interface ModelSchema {
  id: string;
  max_context_tokens: number;
  max_output_tokens: number;
  structured_output: boolean;
  local: boolean;
  free: boolean;
}

export interface ProviderModels {
  provider: string;
  source: string; // "live" | "static" | "cache"
  models: ModelSchema[];
}

export interface ModelsResponse {
  providers: ProviderModels[];
}

export interface RunCreatedResponse {
  run_id: string;
  status: RunStatus;
}

export type RunStatus = "queued" | "running" | "completed" | "failed";

export interface ProgressSchema {
  current_stage: string | null;
  stage_index: number;
  stage_count: number;
  provider: string | null;
  model: string | null;
  model_attempt_number: number;
  request_attempt: number;
  provider_call_number: number;
  models_attempted: number; // retained for compatibility
  recovery_attempts: number;
  message: string;
}

export interface SummarySchema {
  requirements: number;
  business_rules: number;
  scenarios: number;
  test_conditions?: number;
  test_cases: number;
  gaps: number;
  coverage_percent: number;
  requirement_coverage_percent?: number;
  business_rule_coverage_percent?: number;
  scenario_coverage_percent?: number;
  condition_coverage_percent?: number;
  unresolved_conditions?: number;
  expansion_truncated?: boolean;
}

export interface RunStatusResponse {
  run_id: string;
  status: RunStatus;
  entry_point: string | null;
  detection: string | null;
  summary: SummarySchema | null;
  progress: ProgressSchema | null;
  error: string | null;
  failed_stage: string | null;
  recovery_attempts: number | null;
}

export interface ArtifactSchema {
  name: string;
  format: string;
}

export interface ArtifactsResponse {
  run_id: string;
  artifacts: ArtifactSchema[];
}

// --- domain shapes inside the JSON artifact ---------------------------------
// These mirror qaops.models domain models as serialized by the JSON exporter.
// Verified against a real generated artifact - field names are exact.

export type GapSeverity = "blocker" | "major" | "minor";
export type Priority = "critical" | "high" | "medium" | "low";
export type TestType =
  | "functional"
  | "negative"
  | "boundary"
  | "validation"
  | "permission"
  | "state_transition"
  | "integration"
  | "ui"
  | "error_handling";

export interface Requirement {
  id: string;
  title: string;
  description: string;
  actors: string[];
}

export interface BusinessRule {
  id: string;
  requirement_id: string;
  rule: string;
  source_excerpt: string;
}

export interface Gap {
  description: string;
  severity: GapSeverity;
  requirement_id: string;
  suggested_question: string;
}

export interface GapReport {
  gaps: Gap[];
}

export interface Scenario {
  id: string;
  title: string;
  description: string;
  category: string;
  requirement_ids: string[];
}

export interface TestStep {
  number: number;
  action: string;
  expected: string;
}

export interface TestCase {
  id: string;
  scenario_id: string;
  condition_id?: string | null;
  slot_id?: string | null;
  technique?: string | null;
  requirement_ids: string[];
  module: string;
  feature: string;
  title: string;
  objective: string;
  preconditions: string[];
  test_data: Record<string, string>;
  steps: TestStep[];
  expected_result: string;
  priority: Priority;
  test_type: TestType;
  provisional?: boolean;
  tags: string[];
}

export type ConditionStatus = "resolved" | "unresolved";

export interface TestCondition {
  id: string;
  scenario_id: string;
  requirement_ids: string[];
  business_rule_ids: string[];
  category: string;
  description: string;
  rationale: string;
  source_basis: string;
  status: ConditionStatus;
  parameters: Record<string, string>;
  gap_reference: string;
}

export interface CoverageMetrics {
  total_requirements: number;
  covered_requirements: number;
  total_business_rules: number;
  covered_business_rules: number;
  total_scenarios: number;
  covered_scenarios: number;
  total_test_cases: number;
  total_conditions?: number;
  covered_conditions?: number;
  unresolved_conditions?: number;
  expansion_truncated?: boolean;
}

export interface CoverageReport {
  metrics: CoverageMetrics;
  // Other keys (per_requirement, traceability, etc.) exist but are not
  // consumed by the MVP; kept open so extra fields don't break parsing.
  [key: string]: unknown;
}

// The full JSON artifact produced by the JSON exporter.
export interface DesignArtifact {
  source_name: string;
  requirements: Requirement[];
  business_rules: BusinessRule[];
  gap_report: GapReport;
  scenarios: Scenario[];
  conditions?: TestCondition[];
  test_cases: TestCase[];
  coverage: CoverageReport;
  expansion_truncated?: boolean;
  truncation_note?: string;
}
