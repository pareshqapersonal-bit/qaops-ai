"""Adaptive execution: model and provider failover with discovery (ADR-026, ADR-027).

The executor runs pipeline stages and, on failure, exhausts compatible models
within a provider before switching providers, rebuilding the remaining stages
against each new target so no stage learns which provider or model serves it.
Completed stages are checkpointed and never recomputed.

The provider registry describes providers; the model registry discovers and
caches each provider's models with capability metadata. Health is per-run
state held by the executor.
"""

from qaops.execution.executor import (
    AdaptiveExecutor,
    ExecutionReport,
    StageCheckpoint,
)
from qaops.execution.models import (
    ModelHealth,
    ModelInfo,
    ModelRegistry,
    discover_ollama_models,
    discover_openrouter_models,
    filter_by_capability,
    static_models,
)
from qaops.execution.policy import Action, FailureKind, Recovery, classify_failure, recovery_for
from qaops.execution.registry import (
    ProviderHealth,
    ProviderInfo,
    all_providers,
    available_providers,
    get_provider,
    key_variables_for,
)

__all__ = [
    "Action",
    "AdaptiveExecutor",
    "ExecutionReport",
    "FailureKind",
    "ModelHealth",
    "ModelInfo",
    "ModelRegistry",
    "ProviderHealth",
    "ProviderInfo",
    "Recovery",
    "StageCheckpoint",
    "all_providers",
    "available_providers",
    "classify_failure",
    "discover_ollama_models",
    "discover_openrouter_models",
    "filter_by_capability",
    "get_provider",
    "key_variables_for",
    "recovery_for",
    "static_models",
]
