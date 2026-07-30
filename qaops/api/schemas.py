"""HTTP response schemas (ADR-028).

Thin Pydantic models describing what the API returns. They are the API's
contract, deliberately separate from the domain models so an internal change
does not silently reshape the HTTP surface. None of them carry secrets.
"""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class ModelSchema(BaseModel):
    id: str
    max_context_tokens: int
    max_output_tokens: int
    structured_output: bool
    local: bool
    free: bool


class ProviderModelsSchema(BaseModel):
    provider: str
    source: str  # "live", "static", or "cache"
    models: list[ModelSchema]


class ModelsResponse(BaseModel):
    providers: list[ProviderModelsSchema]


class RunCreatedResponse(BaseModel):
    run_id: str
    status: str


class SummarySchema(BaseModel):
    requirements: int
    business_rules: int
    scenarios: int
    test_cases: int
    gaps: int
    coverage_percent: float


class ProgressSchema(BaseModel):
    current_stage: str | None = None
    stage_index: int = 0
    stage_count: int = 0
    provider: str | None = None
    model: str | None = None
    # Disambiguated counters (ADR-030). model_attempt_number is which distinct
    # model this is for the stage; request_attempt is which network request for
    # the current model. models_attempted is retained for compatibility.
    model_attempt_number: int = 0
    request_attempt: int = 0
    # Actual provider generation calls for the stage so far (ADR-030), the
    # honest count including structured-output repair calls.
    provider_call_number: int = 0
    models_attempted: int = 0
    recovery_attempts: int = 0
    message: str = ""


class AttemptSchema(BaseModel):
    """One sanitized failed attempt in the failure history (ADR-035).

    Normalized fields only - no keys, headers, payloads, or raw exception text.
    """

    stage: str
    provider: str
    model: str
    failure_kind: str
    status_code: int | None = None
    error_code: str | None = None


class RunStatusResponse(BaseModel):
    run_id: str
    status: str
    entry_point: str | None = None
    detection: str | None = None
    summary: SummarySchema | None = None
    progress: ProgressSchema | None = None
    error: str | None = None
    failed_stage: str | None = None
    recovery_attempts: int | None = None
    # Ordered sanitized history of failed attempts across providers/models, so a
    # failed run shows the whole failover story, not only the last error.
    attempt_history: list[AttemptSchema] | None = None


class ArtifactSchema(BaseModel):
    name: str
    format: str


class ArtifactsResponse(BaseModel):
    run_id: str
    artifacts: list[ArtifactSchema]


class ErrorResponse(BaseModel):
    detail: str
