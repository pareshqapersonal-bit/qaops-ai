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
from qaops.cli.registry import EXPORTERS, ExporterInstance, resolve_exporters
from qaops.config import QAOpsSettings
from qaops.core.errors import (
    ConfigurationError,
    ExportError,
    InputTooLargeError,
    LLMError,
    QAOpsError,
    StageError,
)
from qaops.llm import PromptLoader, create_client
from qaops.models import RequirementInput, TestDesignResult
from qaops.pipelines.test_design import build_full_pipeline

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
def design(
    input_path: Annotated[
        Path,
        typer.Argument(help="Path to the requirement document (.md or .txt).", show_default=False),
    ],
    output_dir: Annotated[
        Path | None,
        typer.Option("--output-dir", "-o", help="Directory for reports. Overrides config."),
    ] = None,
    formats: Annotated[
        list[str] | None,
        typer.Option(
            "--format",
            "-f",
            help=f"Export format(s). Repeatable. Choices: {', '.join(sorted(EXPORTERS))}.",
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
        _run_design(input_path, output_dir, formats, config_path)
    except (QAOpsError, KeyError) as exc:
        if debug:
            raise
        _fail(_message_for(exc))


def _message_for(exc: Exception) -> str:
    if isinstance(exc, InputTooLargeError):
        return str(exc)
    if isinstance(exc, ConfigurationError):
        return f"Configuration problem. {exc}"
    if isinstance(exc, LLMError):
        return f"The AI provider call failed. {exc}"
    if isinstance(exc, StageError):
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
) -> None:
    if not input_path.exists():
        msg = f"Input file not found: {input_path}"
        raise ConfigurationError(msg)

    settings = load_settings(config_path)
    if output_dir is not None:
        settings = settings.model_copy(update={"output_dir": output_dir})
    export_formats = formats or settings.default_export_formats
    exporters = resolve_exporters(export_formats)

    text = input_path.read_text(encoding="utf-8")
    _echo(f"Reading {input_path} ({len(text)} characters)")
    _echo(f"Provider: {settings.provider} | formats: {', '.join(export_formats)}")

    client = create_client(settings)
    pipeline = build_full_pipeline(client, PromptLoader(version=settings.prompt_version), settings)
    _echo("Running pipeline: analyze -> rules -> gaps -> scenarios -> test cases -> coverage")
    result = pipeline.run(RequirementInput(text=text, source_name=input_path.name))
    assert isinstance(result, TestDesignResult)

    _print_summary(result)
    _write_reports(result, exporters, settings, input_path)


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


def _write_reports(
    result: TestDesignResult,
    exporters: list[ExporterInstance],
    settings: QAOpsSettings,
    input_path: Path,
) -> None:
    out_dir = settings.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = input_path.stem
    _echo("")
    _echo(f"Writing reports to {out_dir}/")
    for exporter in exporters:
        target = out_dir / f"{stem}{exporter.file_extension}"
        written = exporter.export(result, str(target))
        _echo(f"  {exporter.format_name:9s} -> {written}")
    _echo("")
    _echo("Done.")


def main() -> None:
    """Console-script entry point (see [project.scripts] in pyproject.toml)."""
    app()


if __name__ == "__main__":
    main()
