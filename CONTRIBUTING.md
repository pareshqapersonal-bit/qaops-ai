# Contributing to QAOps AI

QAOps AI is developed architecture-first: contracts and decisions come before
code, and every change lands as a small, independently mergeable unit. This
document describes the workflow actually used in the repository — follow it
even for solo work, because the discipline is the point.

## Ground rules

1. **Architecture before implementation.** If a change alters a stage
   boundary, a protocol, a domain model, or the LLM boundary, discuss it (and
   record it — see ADRs below) before writing code.
2. **LLM generates; code validates.** Never move ID assignment, traceability,
   coverage math, or deduplication behind an LLM call. This is ADR-001 and it
   is not negotiable.
3. **Typed models at every boundary.** No raw dicts or raw JSON between
   pipeline stages (ADR-003). New data crossing a boundary means a new or
   extended Pydantic model.
4. **Extend, don't rewrite.** Working functionality is extended through new
   components behind existing protocols. Rewrites require an ADR explaining
   why extension is impossible.
5. **One phase at a time.** The roadmap in CHANGELOG.md is sequential. A
   phase is not started until the previous phase is merged with green CI.

## Development setup

```bash
git clone https://github.com/pareshtester/qaops-ai.git
cd qaops-ai
python -m venv .venv && source .venv/bin/activate   # Python 3.12+
pip install -e ".[dev]"
```

Configuration is environment-driven (`QAOPS_*` variables — see
`.env.example`). Real LLM calls additionally require `ANTHROPIC_API_KEY` in
your environment. Never commit keys; `.env` is gitignored.

## Workflow for every feature

1. Review the requirement; challenge it if it conflicts with an ADR.
2. Design the interface first: which models in, which models out, which
   protocol the component implements.
3. Implement the smallest deployable unit.
4. Add tests (see below) in the same change.
5. Run the full quality gate locally.
6. Update documentation (README/CHANGELOG; new ADR if a decision was made).
7. Commit only when the gate is green. Suggested message format:
   `phase-N: <component> — <what changed>`.

## Quality gate

Every change must pass all of the following locally before commit; CI runs
the identical gate on Python 3.12 and 3.13, and every pull request must be
green before merge:

```bash
ruff check .            # lint
ruff format --check .   # formatting
mypy qaops tests        # strict type checking
pytest -m "not llm"     # unit tests (mocked LLM, no API key needed)
```

No suppressions (`# noqa`, `# type: ignore`) without an inline comment
explaining why.

## Testing standards (ADR-008)

- **Deterministic code** (models, IDs, pipeline, validator, dedup, exporters,
  config): conventional unit tests covering happy paths, invalid inputs, edge
  cases, and failure scenarios. This is where the highest coverage in the
  repo lives.
- **LLM-backed stages:** unit-tested against `MockLLMClient` with canned
  responses. Assert schema validation, retry behavior, and error handling —
  not creative content.
- **Live evals:** mark tests that call a real provider with
  `@pytest.mark.llm`. They are excluded from CI and run locally with
  `pytest -m llm`.

A feature is not complete until its tests pass. A bug fix should arrive with
a regression test that fails without the fix.

## Architecture Decision Records

Significant decisions are recorded in `docs/adr/` using
Context → Decision → Consequences, numbered sequentially. Write an ADR when a
change: adds a dependency, alters a protocol or stage boundary, changes the
LLM boundary or prompt strategy, or reverses a previous decision (mark the
old ADR "Superseded" with a link). State the cost of the decision honestly,
not just the benefit.

## Prompts

Prompt templates in `qaops/prompts/` are versioned files
(`analyzer_v1.md`, ...). Never edit a released prompt version in place — add
`_v2` and switch via `QAOPS_PROMPT_VERSION`, so behavior changes are visible
diffs and old behavior remains reproducible.

## Versioning and releases (SemVer)

- Pre-1.0: minor bumps may break; breaking changes are called out explicitly
  in CHANGELOG.md.
- Every merged change updates the `[Unreleased]` section of CHANGELOG.md.
- A release = version bump in `pyproject.toml` + CHANGELOG entry + git tag.

## Scope guardrails

Before proposing a feature, check the README non-goals and ADR-005/006/007.
Automation code generation, test execution, docx/PDF parsing, persistence,
web UI, and semantic deduplication are deliberately out of scope for v1.0.
Proposals for these belong in an issue tagged `post-v1`, not a pull request.
