"""Application services shared across interfaces (CLI, API).

The design service holds the orchestration both the CLI and the API need, so
neither reimplements the pipeline workflow (ADR-028).
"""

from qaops.services.design_service import (
    DesignArtifact,
    DesignOutcome,
    DesignService,
    fallback_providers,
    summarize,
)

__all__ = [
    "DesignArtifact",
    "DesignOutcome",
    "DesignService",
    "fallback_providers",
    "summarize",
]
