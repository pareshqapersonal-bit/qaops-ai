"""QAOps configuration.

All settings are overridable via environment variables prefixed with
QAOPS_ (e.g. QAOPS_MODEL, QAOPS_TEMPERATURE). The API key is read from
the standard ANTHROPIC_API_KEY variable and is never stored in config
files. Components receive settings by constructor injection - there is
no global settings singleton.
"""

from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class QAOpsSettings(BaseSettings):
    """Runtime configuration for the QAOps platform."""

    model_config = SettingsConfigDict(
        env_prefix="QAOPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM provider
    provider: str = Field(default="anthropic", description="LLM provider key.")
    model: str = Field(
        default="claude-sonnet-4-6",
        description="Model identifier for the anthropic provider.",
    )
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        description="Model identifier used when provider is 'gemini'.",
    )
    openrouter_model: str = Field(
        default="openai/gpt-oss-20b:free",
        description="Model identifier used when provider is 'openrouter'.",
    )
    groq_model: str = Field(
        default="llama-3.3-70b-versatile",
        description="Model identifier used when provider is 'groq'.",
    )
    execution_strategy: str = Field(
        default="any",
        description=(
            "Recovery eligibility strategy: 'any' (default, unrestricted), "
            "'free_first' (exhaust free-eligible candidates before paid), or "
            "'free_only' (only free-eligible candidates; paid providers such as "
            "Anthropic are never invoked)."
        ),
    )
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    max_output_tokens: int = Field(default=8000, ge=256)
    chunking_strategy: str = Field(
        default="adaptive",
        description=(
            "How chunk size is decided: 'adaptive' (default) derives it from "
            "the provider/model's practical output capacity, so no manual "
            "tuning is needed; 'fixed' uses chunk_size verbatim as an "
            "advanced override."
        ),
    )
    chunk_safety_margin: float = Field(
        default=0.8,
        gt=0.0,
        le=1.0,
        description=(
            "Fraction of estimated model capacity to actually use when "
            "chunking adaptively. Lower values produce smaller, safer chunks."
        ),
    )
    chunk_size: int = Field(
        default=6000,
        ge=500,
        description=(
            "Chunk size in characters. Used only when chunking_strategy is "
            "'fixed'; ignored under the adaptive default."
        ),
    )
    chunk_overlap: int = Field(
        default=500,
        ge=0,
        description=(
            "Characters of context repeated between consecutive chunks, so a "
            "requirement spanning a boundary is still seen whole. Used only "
            "when chunking_strategy is 'fixed'; the adaptive strategy scales "
            "overlap with chunk size."
        ),
    )
    evaluation_mode: bool = Field(
        default=False,
        description=(
            "TEMPORARY evaluation feature. When true, the analyzer instructs the "
            "model to extract at most max_requirements requirements and enforces "
            "that cap in code, so a large document fits within a single model "
            "response. Superseded by document chunking in a future release."
        ),
    )
    max_requirements: int = Field(
        default=10,
        ge=1,
        description="Requirement cap applied only when evaluation_mode is true.",
    )
    llm_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Retries after a schema-invalid LLM response before failing loudly.",
    )

    # Adaptive-execution bounds (ADR-029). Live model discovery can surface
    # hundreds of models per provider; these keep recovery bounded so a
    # credit-exhausted account cannot cause hundreds of attempts.
    max_models_per_provider_per_stage: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Distinct discovered models to try for one stage on one "
        "provider before moving to the next provider. Counts model candidates, "
        "not same-model schema retries.",
    )
    max_stage_recovery_attempts: int = Field(
        default=12,
        ge=1,
        le=100,
        description="Total recovery actions (model switches and provider "
        "switches) allowed for a single stage before it fails cleanly. A "
        "second bound across providers, so a chain of providers cannot compound "
        "into a long run.",
    )
    request_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=1800,
        description="Deadline for a single provider generation request. Bounds "
        "request DURATION, complementing the count-based bounds above. QAOps "
        "owns retries (provider SDK retries are disabled), so this is the wall "
        "time of one network attempt, not a multiple of it. Default 60s targets "
        "hanging free-tier models without cutting off legitimately slow ones.",
    )
    max_provider_calls_per_stage: int = Field(
        default=20,
        ge=1,
        le=200,
        description="Hard ceiling on ACTUAL provider generation calls for one "
        "stage, including structured-output repair calls that happen inside a "
        "single stage.run() (ADR-030). This is the backstop that prevents a "
        "hidden multiplier: 5 models x 3 schema-repair attempts = 15 in the "
        "worst case, so 20 leaves headroom while still being finite. When the "
        "budget is spent the stage fails cleanly instead of making more calls.",
    )

    # Input guardrails (chunking is a future milestone; V1 fails fast)
    max_input_chars: int = Field(
        default=60_000,
        ge=1_000,
        description="Hard cap on requirement input size. Oversized input raises "
        "InputTooLargeError with a clear message instead of silently truncating.",
    )

    # Prompts
    prompt_version: str = Field(default="v1", description="Prompt template version to load.")

    # Export
    output_dir: Path = Field(default=Path("output"))
    default_export_formats: list[str] = Field(default_factory=lambda: ["markdown", "json"])

    @field_validator("provider")
    @classmethod
    def _known_provider(cls, value: str) -> str:
        known = {"anthropic", "gemini", "openrouter", "groq", "mock"}
        if value not in known:
            msg = f"Unknown provider {value!r}. Known providers: {sorted(known)}"
            raise ValueError(msg)
        return value

    @field_validator("execution_strategy")
    @classmethod
    def _known_strategy(cls, value: str) -> str:
        known = {"any", "free_first", "free_only"}
        if value not in known:
            msg = f"Unknown execution_strategy {value!r}. Known: {sorted(known)}"
            raise ValueError(msg)
        return value

    @field_validator("default_export_formats")
    @classmethod
    def _known_formats(cls, values: list[str]) -> list[str]:
        known = {"markdown", "csv", "csv-bundle", "xlsx", "json"}
        unknown = [v for v in values if v not in known]
        if unknown:
            msg = f"Unknown export formats {unknown}. Known formats: {sorted(known)}"
            raise ValueError(msg)
        return values

    @model_validator(mode="after")
    def _overlap_smaller_than_chunk(self) -> "QAOpsSettings":
        if self.chunk_overlap >= self.chunk_size:
            msg = (
                f"chunk_overlap ({self.chunk_overlap}) must be smaller than "
                f"chunk_size ({self.chunk_size})."
            )
            raise ValueError(msg)
        return self

    @field_validator("chunking_strategy")
    @classmethod
    def _known_chunking_strategy(cls, value: str) -> str:
        known = {"adaptive", "fixed"}
        if value not in known:
            msg = f"Unknown chunking strategy {value!r}. Known strategies: {sorted(known)}"
            raise ValueError(msg)
        return value
