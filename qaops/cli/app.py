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
from qaops.cli.registry import EXPORTERS, ExporterInstance, resolve_exporters
from qaops.config import QAOpsSettings
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
from qaops.entrypoints import (
    Classification,
    EntryPoint,
    build_pipeline_for,
    classify_input,
    format_issues,
    parse_requirements,
    parse_scenarios,
    preflight,
    stage_names_for,
)
from qaops.exporters import CsvBundleExporter
from qaops.ingestion import load_document
from qaops.llm import PromptLoader, create_client
from qaops.models import (
    RequirementAnalysisResult,
    RequirementInput,
    ScenarioDesignResult,
    TestDesignResult,
)

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
    if not input_path.exists():
        msg = f"Input file not found: {input_path}"
        raise ConfigurationError(msg)

    # Detect the workflow unless the user overrode it (ADR-025).
    detection: Classification | None = None
    if from_ is None:
        detection = classify_input(input_path)
        entry_point = detection.entry_point
    else:
        try:
            entry_point = EntryPoint(from_.strip().casefold())
        except ValueError as exc:
            valid = ", ".join(e.value for e in EntryPoint)
            msg = f"Unknown entry point {from_!r}. Valid options: {valid}."
            raise ConfigurationError(msg) from exc

    settings = load_settings(config_path)
    if output_dir is not None:
        settings = settings.model_copy(update={"output_dir": output_dir})

    # Catch predictable failures before spending any LLM calls.
    issues = preflight(input_path, settings, entry_point)
    if issues:
        raise ConfigurationError(format_issues(issues))

    export_formats = formats or settings.default_export_formats
    # csv-bundle is a directory-writing package, not a single-file Exporter, so
    # it is dispatched separately from the protocol-shaped file exporters.
    want_bundle = CsvBundleExporter.format_name in export_formats
    file_formats = [f for f in export_formats if f != CsvBundleExporter.format_name]
    exporters = resolve_exporters(file_formats)

    # Each entry point produces the domain model its first stage expects; the
    # stages themselves never learn which route was taken (ADR-022).
    pipeline_input: RequirementInput | RequirementAnalysisResult | ScenarioDesignResult
    if entry_point is EntryPoint.REQUIREMENTS:
        analysis = parse_requirements(input_path)
        pipeline_input = analysis
        detail = f"{len(analysis.requirements)} requirements"
    elif entry_point is EntryPoint.SCENARIOS:
        design = parse_scenarios(input_path)
        pipeline_input = design
        detail = f"{len(design.scenarios)} scenarios"
    else:
        text = load_document(input_path)
        pipeline_input = RequirementInput(text=text, source_name=input_path.name)
        detail = f"{len(text)} characters"

    if detection is not None:
        _echo(f"Detected: {detection.description} ({detection.reason})")
    _echo(f"Reading {input_path} ({detail})")
    _echo(f"Provider: {settings.provider} | formats: {', '.join(export_formats)}")

    client = create_client(settings)
    pipeline = build_pipeline_for(
        entry_point, client, PromptLoader(version=settings.prompt_version), settings
    )
    stages = " -> ".join(stage_names_for(entry_point))
    _echo(f"Running pipeline ({entry_point.value}): {stages}")
    result = pipeline.run(pipeline_input)
    assert isinstance(result, TestDesignResult)

    _print_summary(result)
    _write_reports(result, exporters, settings, input_path, write_bundle=want_bundle)


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


def _resolved(path: Path) -> Path:
    """Absolute, symlink-free path for reliable comparison."""
    try:
        return path.resolve()
    except OSError:  # pragma: no cover - resolve is total on supported platforms
        return path.absolute()


def _check_output_collisions(
    exporters: list[ExporterInstance],
    out_dir: Path,
    input_path: Path,
    *,
    write_bundle: bool,
) -> None:
    """Refuse to overwrite the input file with a report.

    Reports are named after the input stem, and csv-bundle uses fixed
    filenames, so running with an input that lives in the output directory can
    silently destroy that input - e.g. reading `output/Requirements.csv` and
    then writing a fresh `output/Requirements.csv` over it. The read happens
    first, so this "works" until a mid-run failure loses the file. Checking
    before any write means nothing is clobbered either way.
    """
    source = _resolved(input_path)
    planned: list[Path] = [out_dir / f"{input_path.stem}{e.file_extension}" for e in exporters]
    if write_bundle:
        planned += [out_dir / name for name in CsvBundleExporter.BUNDLE_FILENAMES]

    clashes = sorted({p.name for p in planned if _resolved(p) == source})
    if clashes:
        msg = (
            "Cannot write reports. The selected output directory contains the "
            f"input file: {input_path}\n\n"
            f"These export(s) would overwrite it: {', '.join(clashes)}\n\n"
            "Choose another output directory, for example:\n"
            f"  --output-dir {out_dir / 'run2'}"
        )
        raise ExportError(msg)


def _friendly_write_error(target: Path, exc: OSError) -> ExportError:
    """Turn a filesystem failure into an actionable message.

    The common case on Windows is a CSV still open in Excel, which surfaces as
    PermissionError. A traceback tells the user nothing useful; naming the file
    and the likely cause does.
    """
    if isinstance(exc, PermissionError):
        msg = (
            f"Unable to write {target}. The file appears to be open in another "
            "application (for example Excel), or you lack permission to write "
            "there. Close the file and retry, or choose a different output "
            "directory with --output-dir."
        )
    elif isinstance(exc, IsADirectoryError):
        msg = f"Unable to write {target}: a directory already exists at that path."
    else:
        msg = f"Unable to write {target}: {exc}"
    return ExportError(msg)


def _write_reports(
    result: TestDesignResult,
    exporters: list[ExporterInstance],
    settings: QAOpsSettings,
    input_path: Path,
    *,
    write_bundle: bool = False,
) -> None:
    out_dir = settings.output_dir
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _friendly_write_error(out_dir, exc) from exc
    _check_output_collisions(exporters, out_dir, input_path, write_bundle=write_bundle)
    stem = input_path.stem
    _echo("")
    _echo(f"Writing reports to {out_dir}/")
    for exporter in exporters:
        target = out_dir / f"{stem}{exporter.file_extension}"
        try:
            written = exporter.export(result, str(target))
        except OSError as exc:
            raise _friendly_write_error(target, exc) from exc
        _echo(f"  {exporter.format_name:11s} -> {written}")
    if write_bundle:
        try:
            bundle_paths = CsvBundleExporter().export_bundle(result, out_dir)
        except OSError as exc:
            raise _friendly_write_error(out_dir, exc) from exc
        for path in bundle_paths:
            _echo(f"  {'csv-bundle':11s} -> {path}")
    _echo("")
    _echo("Done.")


def main() -> None:
    """Console-script entry point (see [project.scripts] in pyproject.toml)."""
    app()


if __name__ == "__main__":
    main()
