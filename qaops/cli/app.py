"""The QAOps command-line interface.

A thin composition root (ADR-017): it parses arguments, loads settings,
runs the existing full pipeline, and writes the configured export
formats. It contains no requirement-analysis, generation, validation, or
serialization logic - all of that lives in the pipeline and exporters it
calls.

    qaops design examples/login.md
    qaops design spec.md --format json --format markdown --output-dir out
"""

from pathlib import Path
from typing import Annotated

import typer

from qaops.cli.config_loader import load_settings
from qaops.cli.diagnostics import diagnose_provider_error
from qaops.cli.registry import EXPORTERS
from qaops.core.errors import (
    ConfigurationError,
    DocumentLoadError,
    ExportError,
    InputTooLargeError,
    LLMError,
    QAOpsError,
    StageError,
    UnsupportedDocumentFormatError,
)
from qaops.execution import (
    ModelRegistry,
    available_providers,
)
from qaops.exporters import CsvBundleExporter
from qaops.models import (
    TestDesignResult,
)
from qaops.services import DesignService

app = typer.Typer(
    name="qaops",
    help="QAOps AI - generate manual test design from a requirement document.",
    add_completion=False,
    no_args_is_help=True,
)


@app.callback()
def _root() -> None:
    """QAOps AI command-line interface.

    A callback with no logic, present only so that `design` stays a named
    subcommand (`qaops design <input>`) rather than collapsing into the
    root command, which single-command Typer apps do by default.
    """


def _echo(message: str) -> None:
    typer.echo(message)


def _fail(message: str, code: int = 1) -> None:
    """Print a friendly error to stderr and exit with a nonzero code."""
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code)


@app.command()
def models(
    refresh: Annotated[
        bool,
        typer.Option("--refresh", help="Force rediscovery instead of using cached results."),
    ] = False,
    no_discovery: Annotated[
        bool,
        typer.Option("--static", help="Show only the curated model table, skipping discovery."),
    ] = False,
) -> None:
    """List the models each available provider can serve.

    Discovers models via provider APIs where available, falling back to a
    curated table when a provider is unreachable. A standalone way to verify
    discovery without running a pipeline.
    """
    registry = ModelRegistry(discovery_enabled=not no_discovery)
    if refresh:
        registry.refresh()

    providers = available_providers()
    if not providers:
        _echo("No providers available. Set an API key (e.g. OPENROUTER_API_KEY) and retry.")
        return

    for info in providers:
        discovered = registry.models_for(info.name)
        _echo("")
        _echo(f"Provider: {info.name}")
        if not discovered:
            _echo("  (no models found)")
            continue
        _echo(f"  {len(discovered)} model(s):")
        for model in discovered:
            flags = []
            if model.free:
                flags.append("free")
            if model.local:
                flags.append("local")
            suffix = f" [{', '.join(flags)}]" if flags else ""
            _echo(
                f"    - {model.name}"
                f" (context {model.max_context_tokens:,}, out {model.max_output_tokens:,})"
                f"{suffix}"
            )


@app.command()
def design(
    input_path: Annotated[
        Path,
        typer.Argument(
            help=(
                "Input file: a requirement document (.pdf, .docx, .md, .txt), or a "
                "requirements/scenarios file (.csv, .json, .xlsx). The workflow is "
                "detected automatically."
            ),
            show_default=False,
        ),
    ],
    from_: Annotated[
        str | None,
        typer.Option(
            "--from",
            help=(
                "Override the detected entry point: 'document' (full pipeline), "
                "'requirements' (skip analysis), or 'scenarios' (test cases only). "
                "Detected automatically when omitted."
            ),
        ),
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Directory for reports. Overrides config."),
    ] = None,
    formats: Annotated[
        list[str] | None,
        typer.Option(
            "--format",
            "-f",
            help=(
                "Export format(s). Repeatable. Choices: "
                f"{', '.join(sorted(EXPORTERS))}, {CsvBundleExporter.format_name}."
            ),
        ),
    ] = None,
    config_path: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="Path to qaops.yaml. Defaults to ./qaops.yaml."),
    ] = None,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Re-raise errors with a full traceback."),
    ] = False,
) -> None:
    """Process a requirement document into test design reports."""
    try:
        _run_design(input_path, output_dir, formats, config_path, from_)
    except (QAOpsError, KeyError) as exc:
        if debug:
            raise
        _fail(_message_for(exc))


def _message_for(exc: Exception) -> str:
    if isinstance(exc, UnsupportedDocumentFormatError):
        lines = [str(exc), "", "Supported formats:"]
        lines += [f"  - {ext}" for ext in exc.supported]
        if exc.install_hint:
            lines += ["", exc.install_hint]
        return "\n".join(lines)
    if isinstance(exc, DocumentLoadError):
        return f"Could not read the document. {exc}"
    if isinstance(exc, InputTooLargeError):
        return str(exc)
    if isinstance(exc, ConfigurationError):
        return f"Configuration problem. {exc}"
    if isinstance(exc, LLMError):
        diagnosis = diagnose_provider_error(str(exc))
        if diagnosis is not None:
            return "The AI provider call failed.\n\n" + diagnosis.render(str(exc))
        return f"The AI provider call failed. {exc}"
    if isinstance(exc, StageError):
        # A provider failure inside a stage arrives wrapped, so the raw HTTP
        # body would otherwise leak through the stage message.
        diagnosis = diagnose_provider_error(str(exc))
        if diagnosis is not None:
            return "The AI provider call failed.\n\n" + diagnosis.render(str(exc))
        return f"A pipeline stage failed. {exc}"
    if isinstance(exc, ExportError):
        return f"Export failed. {exc}"
    if isinstance(exc, KeyError):
        # resolve_exporters raises KeyError with a ready message.
        return str(exc).strip("\"'")
    return str(exc)


def _run_design(
    input_path: Path,
    output_dir: Path | None,
    formats: list[str] | None,
    config_path: Path | None,
    from_: str | None = None,
) -> None:
    settings = load_settings(config_path)
    if output_dir is not None:
        settings = settings.model_copy(update={"output_dir": output_dir})

    # The orchestration lives in DesignService, shared with the API (ADR-028).
    # The CLI supplies terminal output as the progress reporter and keeps its
    # own summary rendering.
    service = DesignService()
    outcome = service.run(
        input_path,
        settings,
        from_=from_,
        formats=formats,
        report=_echo,
    )
    _print_summary(outcome.result)


def _print_summary(result: TestDesignResult) -> None:
    m = result.coverage.metrics
    _echo("")
    _echo("Summary")
    _echo(f"  Requirements:   {m.total_requirements} ({m.requirement_coverage_pct}% covered)")
    _echo(f"  Business rules: {m.total_business_rules} ({m.business_rule_coverage_pct}% covered)")
    _echo(f"  Scenarios:      {m.total_scenarios} ({m.scenario_coverage_pct}% covered)")
    _echo(f"  Test cases:     {m.total_test_cases}")
    gaps = result.gap_report.gaps
    if gaps:
        blockers = sum(1 for g in gaps if g.severity.value == "blocker")
        _echo(f"  Gaps:           {len(gaps)} ({blockers} blocker(s))")
    uncovered = result.coverage.uncovered_requirement_ids
    if uncovered:
        _echo(f"  Uncovered reqs: {', '.join(uncovered)}")
    if result.coverage.duplicate_pairs:
        _echo(f"  Suspected duplicate test cases: {len(result.coverage.duplicate_pairs)}")


def main() -> None:
    """Console-script entry point (see [project.scripts] in pyproject.toml)."""
    app()


if __name__ == "__main__":
    main()
