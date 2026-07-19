# ADR-005: Defer plugin registry and storage; protocols only

**Status:** Accepted · **Date:** 2026-07-10

## Context

The original spec proposed a twelve-package structure including `storage/`
and an `agents/` plugin architecture sized for nine future agents. V1 ships
one agent, and nothing in V1 persists beyond exported files. Building agent
discovery, inter-agent communication, and a storage layer now would be
speculative architecture with no consumer.

## Decision

- Future extensibility is provided by exactly two small protocols in
  `qaops/core/protocols.py`: `Agent` and `PipelineStage` (plus `Exporter`).
  A future agent implements `Agent` and reuses `llm/`, `models/`,
  `exporters/`, `config/` unchanged.
- No `storage/` package in V1. No plugin registry, no discovery mechanism.
  When a second agent exists, registration starts as a plain dict; a registry
  is promoted only when real reuse patterns demand it.
- The V1 package is named `pipelines/test_design/`, not `agents/`: with a
  single agent composed of stages, calling everything an "agent" blurs
  responsibilities. `agents/` is introduced when a second independent AI
  capability ships.

## Consequences

- Smaller, honest V1 surface; nothing to maintain that nothing uses.
- Adding Agent #2 requires zero changes to existing modules — the protocols
  already exist and are runtime-checkable.
- Cost: a rename/move when the agent layer materializes. Accepted — a
  mechanical refactor later beats dead scaffolding now.

