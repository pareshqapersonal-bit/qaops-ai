# ADR-004: Python 3.12 minimum, not 3.14

**Status:** Accepted · **Date:** 2026-07-10

## Context

The original spec mandated Python 3.14+. It shipped in October 2025 and parts
of the ecosystem (type-checker plugins, some libraries, CI runner images)
still lag. This is also a portfolio project: reviewers and employers must be
able to run it without installing a bleeding-edge interpreter.

## Decision

Minimum Python 3.12; CI tests 3.12 and 3.13. No 3.13+-only syntax.

## Consequences

- Broader compatibility today; the floor can be raised later without breaking
  anyone (the reverse is not true).
- We forgo 3.13/3.14-only features. Nothing in V1 needs them.
