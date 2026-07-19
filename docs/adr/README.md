# Architecture Decision Records

| ADR | Title | Status |
|---|---|---|
| [001](001-llm-generates-code-validates.md) | LLM generates, code validates | Accepted |
| [002](002-single-llm-boundary.md) | Single LLM boundary behind an `LLMClient` interface | Accepted |
| [003](003-typed-models-between-stages.md) | Strict typed Pydantic models between all pipeline stages | Accepted |
| [004](004-python-312-floor.md) | Python 3.12 minimum, not 3.14 | Accepted |
| [005](005-defer-agent-registry-and-storage.md) | Defer plugin registry and storage; protocols only | Accepted |
| [006](006-text-input-only-v1.md) | Plain text / Markdown input only in V1 | Accepted |
| [007](007-heuristic-deduplication.md) | Heuristic deduplication that flags, never deletes | Accepted |
| [008](008-testing-strategy.md) | Testing strategy for non-deterministic components | Accepted |
| [009](009-configuration.md) | Configuration via pydantic-settings, constructor injection | Accepted |

ADR format: Context → Decision → Consequences. A superseding ADR must link back to the ADR it replaces.
