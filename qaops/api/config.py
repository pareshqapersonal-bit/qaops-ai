"""API-layer configuration (ADR-028).

Separate from QAOpsSettings, which configures the pipeline. This configures the
HTTP surface: where run workspaces live and which browser origins may call the
API. Both are environment-overridable so local development needs no code edit.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

# Local frontend origins the next phase is expected to use. Explicit, never "*"
# with credentials (section 12).
_DEFAULT_ORIGINS = (
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
)


def _default_runtime_dir() -> Path:
    override = os.environ.get("QAOPS_RUNTIME_DIR")
    if override:
        return Path(override)
    return Path.home() / ".qaops" / "runs"


def _configured_origins() -> list[str]:
    raw = os.environ.get("QAOPS_CORS_ORIGINS")
    if not raw:
        return list(_DEFAULT_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@dataclass
class APIConfig:
    """Runtime configuration for the API process."""

    runtime_dir: Path = field(default_factory=_default_runtime_dir)
    cors_origins: list[str] = field(default_factory=_configured_origins)
