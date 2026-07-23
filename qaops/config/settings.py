"""QAOps configuration.

All settings are overridable via environment variables prefixed with
QAOPS_ (e.g. QAOPS_MODEL, QAOPS_TEMPERATURE). The API key is read from
the standard ANTHROPIC_API_KEY variable and is never stored in config
files. Components receive settings by constructor injection - there is
no global settings singleton.
"""

from pathlib import Path

from pydantic import Field, field_validator
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
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    max_output_tokens: int = Field(default=8000, ge=256)
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
        known = {"anthropic", "gemini", "openrouter", "mock"}
        if value not in known:
            msg = f"Unknown provider {value!r}. Known providers: {sorted(known)}"
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
