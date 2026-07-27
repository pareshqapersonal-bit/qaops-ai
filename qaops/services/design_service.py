"""Design orchestration shared by the CLI and the API (ADR-028).

`_run_design` in the CLI wove three concerns together: orchestration (classify,
preflight, parse, build, execute), terminal output (`_echo`), and report
writing. The API needs the orchestration without the terminal output, so that
middle layer is extracted here and progress is emitted through a callback the
caller supplies - the CLI passes its echo function, the API captures the lines
into a run log.

This is the minimal extraction section 8 of the phase asks for: no behaviour
changes, no broad refactor. The service coordinates existing components
(classifier, preflight, parsers, registry, PipelineBuilder, AdaptiveExecutor,
exporters) and returns a domain result. It knows nothing about Typer or HTTP.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from qaops.cli.registry import ExporterInstance, resolve_exporters
from qaops.config import QAOpsSettings
from qaops.core.errors import ConfigurationError, ExportError
from qaops.core.protocols import PipelineStage
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
from qaops.execution import (
    AdaptiveExecutor,
    ModelRegistry,
    ProviderInfo,
    available_providers,
    get_provider,
)
from qaops.execution.events import EventSink
from qaops.exporters import CsvBundleExporter
from qaops.ingestion import load_document
from qaops.llm import PromptLoader, create_client
from qaops.models import (
    RequirementAnalysisResult,
    RequirementInput,
    ScenarioDesignResult,
    TestDesignResult,
)

# A sink for human-readable progress lines. The CLI passes its echo; the API
# passes a list append. Defaults to discarding, so the service is usable with
# no wiring.
ProgressReporter = Callable[[str], None]


def fallback_providers(settings: QAOpsSettings) -> list[ProviderInfo]:
    """The provider chain for a run: configured provider first, then others.

    Configuration wins - the chosen provider leads - and automatic discovery
    supplies the rest, so failover works with no extra setup while an explicit
    choice is honoured (ADR-026).
    """
    configured = get_provider(settings.provider)
    chain: list[ProviderInfo] = []
    if configured is not None:
        chain.append(configured)
    for info in available_providers():
        if all(info.name != existing.name for existing in chain):
            chain.append(info)
    return chain


@dataclass
class DesignArtifact:
    """One report the service wrote."""

    name: str
    format: str
    path: Path


@dataclass
class DesignOutcome:
    """Everything a caller needs after a successful design run."""

    result: TestDesignResult
    entry_point: EntryPoint
    detection: Classification | None
    artifacts: list[DesignArtifact] = field(default_factory=list)


class DesignService:
    """Runs the QAOps design workflow for one input, adaptively.

    The service does not print, raise Typer errors, or touch HTTP. It reports
    progress through the supplied callback and raises domain errors
    (ConfigurationError, StageError, DocumentLoadError) that each interface maps
    to its own conventions.
    """

    def __init__(self, *, registry: ModelRegistry | None = None) -> None:
        # One registry per service instance caches discovery for the process,
        # matching the CLI's per-run construction.
        self._registry = registry if registry is not None else ModelRegistry()

    def resolve_entry_point(
        self, input_path: Path, from_: str | None
    ) -> tuple[EntryPoint, Classification | None]:
        """Detect the entry point, or honour an explicit override (ADR-025)."""
        if from_ is not None:
            try:
                return EntryPoint(from_.strip().casefold()), None
            except ValueError as exc:
                valid = ", ".join(e.value for e in EntryPoint)
                msg = f"Unknown entry point {from_!r}. Valid options: {valid}."
                raise ConfigurationError(msg) from exc
        detection = classify_input(input_path)
        return detection.entry_point, detection

    def run(
        self,
        input_path: Path,
        settings: QAOpsSettings,
        *,
        from_: str | None = None,
        formats: Sequence[str] | None = None,
        report: ProgressReporter | None = None,
        events: EventSink | None = None,
    ) -> DesignOutcome:
        """Classify, validate, run the pipeline adaptively, and write reports."""
        emit = report or (lambda _line: None)
        emit_event = events or (lambda _event: None)

        if not input_path.exists():
            msg = f"Input file not found: {input_path}"
            raise ConfigurationError(msg)

        entry_point, detection = self.resolve_entry_point(input_path, from_)

        issues = preflight(input_path, settings, entry_point)
        if issues:
            raise ConfigurationError(format_issues(issues))

        export_formats = list(formats) if formats else list(settings.default_export_formats)
        want_bundle = CsvBundleExporter.format_name in export_formats
        file_formats = [f for f in export_formats if f != CsvBundleExporter.format_name]
        exporters = resolve_exporters(file_formats)

        pipeline_input, detail = self._prepare_input(input_path, entry_point)

        if detection is not None:
            emit(f"Detected: {detection.description} ({detection.reason})")
        emit(f"Reading {input_path} ({detail})")
        emit(f"Provider: {settings.provider} | formats: {', '.join(export_formats)}")

        result = self._execute(entry_point, pipeline_input, settings, emit, emit_event)

        artifacts = self._write_reports(
            result, exporters, settings, input_path, want_bundle=want_bundle, emit=emit
        )
        return DesignOutcome(
            result=result,
            entry_point=entry_point,
            detection=detection,
            artifacts=artifacts,
        )

    # --- internals -----------------------------------------------------------

    def _prepare_input(
        self, input_path: Path, entry_point: EntryPoint
    ) -> tuple[RequirementInput | RequirementAnalysisResult | ScenarioDesignResult, str]:
        """Produce the domain model the entry point's first stage expects."""
        if entry_point is EntryPoint.REQUIREMENTS:
            analysis = parse_requirements(input_path)
            return analysis, f"{len(analysis.requirements)} requirements"
        if entry_point is EntryPoint.SCENARIOS:
            design = parse_scenarios(input_path)
            return design, f"{len(design.scenarios)} scenarios"
        text = load_document(input_path)
        return RequirementInput(text=text, source_name=input_path.name), f"{len(text)} characters"

    def _execute(
        self,
        entry_point: EntryPoint,
        pipeline_input: BaseModel,
        settings: QAOpsSettings,
        emit: ProgressReporter,
        emit_event: EventSink,
    ) -> TestDesignResult:
        """Run through the adaptive executor (single- or multi-provider)."""
        emit(f"Running pipeline ({entry_point.value}): {' -> '.join(stage_names_for(entry_point))}")

        # All execution flows through the adaptive executor, single-provider or
        # multi (ADR-030). The executor handles a one-provider list fine, and
        # routing everything through it means single-provider runs emit the same
        # structured progress events as failover runs - closing the Phase 16.1
        # single-provider progress gap without changing pipeline semantics.
        candidates = fallback_providers(settings)
        if len(candidates) > 1:
            emit(f"Provider failover enabled: {', '.join(i.name for i in candidates)}")

        def build_stages(
            stage_settings: QAOpsSettings,
        ) -> list[PipelineStage[BaseModel, BaseModel]]:
            stage_client = create_client(stage_settings)
            built = build_pipeline_for(
                entry_point,
                stage_client,
                PromptLoader(version=stage_settings.prompt_version),
                stage_settings,
            )
            return list(built.stages)

        executor = AdaptiveExecutor(
            candidates,
            settings,
            build_stages,
            registry=self._registry,
            reporter=emit,
            events=emit_event,
        )
        result = executor.run(pipeline_input)

        if not isinstance(result, TestDesignResult):  # pragma: no cover - defensive
            msg = "Pipeline did not produce a TestDesignResult"
            raise ConfigurationError(msg)
        return result

    def _write_reports(
        self,
        result: TestDesignResult,
        exporters: list[ExporterInstance],
        settings: QAOpsSettings,
        input_path: Path,
        *,
        want_bundle: bool,
        emit: ProgressReporter,
    ) -> list[DesignArtifact]:
        """Write reports through the existing exporters, returning metadata.

        Preserves ADR-023 workflow safety: refuse to overwrite the input, and
        turn filesystem failures into actionable messages.
        """
        out_dir = settings.output_dir
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise _friendly_write_error(out_dir, exc) from exc
        _check_output_collisions(exporters, out_dir, input_path, want_bundle=want_bundle)
        stem = input_path.stem
        artifacts: list[DesignArtifact] = []

        emit(f"Writing reports to {out_dir}/")
        for exporter in exporters:
            target = out_dir / f"{stem}{exporter.file_extension}"
            try:
                written = exporter.export(result, str(target))
            except OSError as exc:
                raise _friendly_write_error(target, exc) from exc
            written_path = Path(written)
            artifacts.append(
                DesignArtifact(
                    name=written_path.name, format=exporter.format_name, path=written_path
                )
            )
            emit(f"  {exporter.format_name:11s} -> {written}")

        if want_bundle:
            try:
                bundle_paths = CsvBundleExporter().export_bundle(result, out_dir)
            except OSError as exc:
                raise _friendly_write_error(out_dir, exc) from exc
            for path in bundle_paths:
                bundle_path = Path(path)
                artifacts.append(
                    DesignArtifact(name=bundle_path.name, format="csv", path=bundle_path)
                )
                emit(f"  {'csv-bundle':11s} -> {path}")

        emit("Done.")
        return artifacts


def _resolved(path: Path) -> Path:
    """Absolute, symlink-free path for reliable identity comparison."""
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def _check_output_collisions(
    exporters: list[ExporterInstance],
    out_dir: Path,
    input_path: Path,
    *,
    want_bundle: bool,
) -> None:
    """Refuse to overwrite the input file with a report (ADR-023).

    Reports are named after the input stem and csv-bundle uses fixed filenames,
    so an input living in the output directory can be silently destroyed. The
    read happens first, so this "works" until a mid-run failure loses the file.
    Checking before any write means nothing is clobbered either way.
    """
    source = _resolved(input_path)
    planned: list[Path] = [out_dir / f"{input_path.stem}{e.file_extension}" for e in exporters]
    if want_bundle:
        planned += [out_dir / name for name in CsvBundleExporter.BUNDLE_FILENAMES]

    clashes = sorted({p.name for p in planned if _resolved(p) == source})
    if clashes:
        msg = (
            "Cannot write reports. The selected output directory contains the "
            f"input file: {input_path}\n\n"
            f"These export(s) would overwrite it: {', '.join(clashes)}\n\n"
            "Choose another output directory."
        )
        raise ExportError(msg)


def _friendly_write_error(target: Path, exc: OSError) -> ExportError:
    """Turn a filesystem failure into an actionable message (ADR-023)."""
    if isinstance(exc, PermissionError):
        msg = (
            f"Unable to write {target}. The file appears to be open in another "
            "application (for example Excel), or you lack permission to write "
            "there. Close the file and retry, or choose a different output "
            "directory."
        )
    elif isinstance(exc, IsADirectoryError):
        msg = f"Unable to write {target}: a directory already exists at that path."
    else:
        msg = f"Unable to write {target}: {exc}"
    return ExportError(msg)


def summarize(result: TestDesignResult) -> dict[str, float | int]:
    """Derive a flat summary from the domain result - no recomputation.

    Every value comes from the coverage metrics or gap report the pipeline
    already produced, so the API and any other caller report identical numbers.
    """
    metrics = result.coverage.metrics
    return {
        "requirements": metrics.total_requirements,
        "business_rules": metrics.total_business_rules,
        "scenarios": metrics.total_scenarios,
        "test_cases": metrics.total_test_cases,
        "gaps": len(result.gap_report.gaps),
        "coverage_percent": metrics.requirement_coverage_pct,
    }
