"""Pre-flight checks run before any pipeline work (ADR-025).

Catches the predictable failures - missing file, absent API key, an optional
dependency that is not installed - before a single LLM call is made, so the
user gets one actionable message instead of a failure several stages in.

Every check is deterministic and cheap. Output-collision safety is already
enforced in the CLI at write time (ADR-023); repeating it here would duplicate
that logic, so preflight covers only what cannot be discovered later without
wasted work.
"""

from dataclasses import dataclass
from pathlib import Path

from qaops.config import QAOpsSettings
from qaops.entrypoints.entry_point import EntryPoint

# Provider -> the environment variable holding its key.
_PROVIDER_KEY_VARS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEY",),
}

# Extensions that need an optional dependency, and the extra that provides it.
_EXTENSION_REQUIREMENTS: dict[str, tuple[str, str]] = {
    ".pdf": ("pypdf", "pdf"),
    ".xlsx": ("openpyxl", "excel"),
    ".xlsm": ("openpyxl", "excel"),
}


@dataclass(frozen=True)
class PreflightIssue:
    """One problem found before execution, with how to fix it."""

    problem: str
    fix: str


def _missing_dependency(path: Path) -> PreflightIssue | None:
    import importlib.util

    requirement = _EXTENSION_REQUIREMENTS.get(path.suffix.lower())
    if requirement is None:
        return None
    module, extra = requirement
    if importlib.util.find_spec(module) is not None:
        return None
    return PreflightIssue(
        problem=(
            f"Reading {path.suffix} files requires the '{module}' package, which is not installed."
        ),
        fix=f"pip install 'qaops-ai[{extra}]'",
    )


def _missing_api_key(settings: QAOpsSettings) -> PreflightIssue | None:
    import os

    if settings.provider == "mock":
        return None
    variables = _PROVIDER_KEY_VARS.get(settings.provider)
    if variables is None:
        return None
    if any(os.environ.get(name, "").strip() for name in variables):
        return None
    names = " or ".join(variables)
    return PreflightIssue(
        problem=f"No API key found for provider '{settings.provider}'.",
        fix=f"Set the {names} environment variable in this shell.",
    )


def preflight(path: Path, settings: QAOpsSettings, entry_point: EntryPoint) -> list[PreflightIssue]:
    """Return every problem that would stop this run, or an empty list."""
    issues: list[PreflightIssue] = []

    if not path.exists():
        issues.append(
            PreflightIssue(
                problem=f"Input file not found: {path}",
                fix="Check the path and try again.",
            )
        )
        return issues  # further checks are meaningless without the file

    if path.is_dir():
        issues.append(
            PreflightIssue(
                problem=f"{path} is a directory, not a file.",
                fix="Pass the path to a requirement or scenario file.",
            )
        )
        return issues

    dependency = _missing_dependency(path)
    if dependency is not None:
        issues.append(dependency)

    key = _missing_api_key(settings)
    if key is not None:
        issues.append(key)

    return issues


def format_issues(issues: list[PreflightIssue]) -> str:
    """Render preflight problems as one actionable message."""
    if len(issues) == 1:
        return f"{issues[0].problem}\n\nTo fix: {issues[0].fix}"
    lines = ["Cannot start the run:"]
    for index, issue in enumerate(issues, start=1):
        lines.append(f"  {index}. {issue.problem}")
        lines.append(f"     Fix: {issue.fix}")
    return "\n".join(lines)
