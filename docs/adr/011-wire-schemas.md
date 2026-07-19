# ADR-011: Wire schemas are separate from domain models

**Status:** Accepted · **Date:** 2026-07-19

## Context

ADR-001 requires IDs to come from code, never the LLM. But the domain models
(`Requirement`, `BusinessRule`) have mandatory, pattern-validated ID fields.
If the LLM were asked to emit domain models directly, it would have to invent
IDs — the exact failure ADR-001 forbids — or the ID fields would need to be
made optional, weakening every downstream consumer.

## Decision

Each LLM-backed stage owns a pair of schemas:

- **Wire schema** (`pipelines/test_design/schemas.py`): what the model must
  return. ID-less, strict (`extra="forbid"`), validated inside
  `generate_structured()` so a malformed response triggers the repair retry
  loop. The only IDs a wire object may contain are *references* to IDs the
  prompt supplied (e.g. a rule's `requirement_id`).
- **Domain model** (`qaops/models/`): what the rest of the system consumes.
  Stage code maps wire → domain, assigning IDs from `IdGenerator` in order,
  and verifies every reference against the known ID set. An unknown
  reference raises `StageError` listing the offending IDs — fail loudly,
  never drop silently.

## Consequences

- ADR-001 is enforced structurally: there is no code path by which a
  model-invented ID reaches a domain model.
- Reference verification catches a whole class of hallucination (linking to
  a nonexistent requirement) at the stage boundary with a debuggable error.
- Cost: each stage maintains two similar-looking schemas. Accepted — the
  duplication is the boundary, and the two evolve for different reasons
  (prompt contract vs. internal API).
