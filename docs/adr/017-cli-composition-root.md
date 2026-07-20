# ADR-017: The CLI is a thin composition root over existing components

**Status:** Accepted · **Date:** 2026-07-20 · **Relates to:** ADR-005, ADR-009, ADR-016

## Context

Phase 7 turns the library into a command-line tool: `qaops design <input>`
must run the whole pipeline and write reports without the user writing Python.
The risk is that a CLI accretes logic — input parsing rules, coverage
formatting, format decisions — that belongs in the library, leaving two places
to change when behavior evolves.

## Decision

1. **The CLI holds no business logic.** `qaops/cli/app.py` parses arguments,
   loads settings, calls `build_full_pipeline(...).run(...)`, and loops the
   exporters. Requirement analysis, generation, validation, and serialization
   stay entirely in the pipeline and exporters it invokes. The console entry
   point (`qaops = qaops.cli.app:main`) was reserved in `pyproject.toml` back
   in Phase 0 and is only now implemented.

2. **Typer, with `design` forced to stay a subcommand.** Typer is type-hint
   native (suits `--strict`) and gives help and usage errors for free. A
   single-command Typer app collapses the command into the root; a no-op
   `@app.callback()` keeps `design` a named subcommand so the required
   `qaops design <input>` form works.

3. **`qaops.yaml` layers *under* the existing settings (ADR-009), not around
   them.** The loader reads the file into a dict and constructs
   `QAOpsSettings(**values)`, so all existing validation applies unchanged and
   no settings model is modified. Because init kwargs are pydantic-settings'
   highest precedence, file keys already present as `QAOPS_*` environment
   variables are dropped before construction — preserving the intended order
   **environment > file > defaults**. Unknown keys are rejected so typos fail
   loudly.

4. **Friendly errors, not tracebacks.** Each library exception
   (`ConfigurationError`, `InputTooLargeError`, `LLMError`, `StageError`,
   `ExportError`, and the registry's `KeyError`) maps to a one-line message
   and a nonzero exit. `--debug` re-raises the original for developers.

5. **Typer and PyYAML are base dependencies, not extras.** The CLI is the
   primary deliverable of this phase; a command-line tool whose command does
   not install without an extra flag would be poor design. They are declared in
   `[project.dependencies]`, so `pip install qaops-ai` alone installs a working
   `qaops` command. This was verified in a clean virtual environment isolated
   from the development machine: with typer/pyyaml/qaops all absent beforehand,
   installing only the wheel pulled `typer` and `PyYAML` (and typer's own
   transitive deps) automatically, after which `qaops --help`,
   `qaops design --help`, and a full `qaops design examples/login.md` run —
   producing markdown/json/csv reports on disk — all succeeded. (Gemini and
   Excel remain optional extras because they are optional capabilities.)

6. **The format→exporter map is a plain dict**, the same "dict until a concrete
   need justifies a registry" judgment as ADR-005.

## Consequences

- One place owns each behavior: the CLI wires, the library computes. Adding a
  pipeline stage or exporter needs no CLI change beyond registration.
- The CLI is fully testable offline: `create_client` is patched to a
  `MockLLMClient`, so the whole command runs in CI with no API key.
- Cost: the concrete exporter classes narrow `export`'s parameter to
  `TestDesignResult`, so they are not structural subtypes of the looser
  `Exporter` protocol; the registry therefore types instances as the concrete
  union rather than the protocol. Accepted — it is precise and honest about
  what the exporters actually accept.
