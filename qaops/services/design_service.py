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
from qaops.core.errors import ConfigurationError, ExportError, StageError
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
from qaops.execution.checkpoint import CheckpointStore
from qaops.execution.events import EventSink
from qaops.execution.executor import CheckpointSink
from qaops.exporters import CsvBundleExporter
from qaops.ingestion import load_document
from qaops.ingestion.evidence import EvidencePackage
from qaops.ingestion.evidence_sidecar import load_evidence_package
from qaops.llm import PromptLoader, create_client
from qaops.models import (
    ConditionDesignResult,
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

        # Phase 36B Part 2: reconstruct any image evidence persisted with the run.
        # The sidecar lives in the run workspace beside output/ (output_dir is
        # workspace/output). A missing sidecar yields None, so text/document-only
        # runs are unchanged. A corrupt sidecar raises, failing the run clearly
        # rather than silently downgrading to text-only. Only the analyzer (stage 1)
        # receives this evidence.
        evidence = load_evidence_package(settings.output_dir.parent)

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

        # Checkpoint each completed stage into the run workspace so partial
        # artifacts survive a later failure and the run can resume (ADR-040).
        # Checkpoints live under the output dir; a run with no workspace still
        # works - the store just writes beside the reports.
        checkpoints = CheckpointStore(settings.output_dir)

        def _checkpoint(stage_name: str, stage_index: int, output: BaseModel) -> None:
            checkpoints.write_stage(stage_name, stage_index, output)

        try:
            result = self._execute(
                entry_point,
                pipeline_input,
                settings,
                emit,
                emit_event,
                checkpoint=_checkpoint,
                evidence=evidence,
            )
        except StageError as exc:
            # Never discard completed work: export whatever the furthest
            # checkpoint produced, then re-raise so the caller records the
            # failure. Downstream artifacts simply remain unavailable.
            self._export_partial(checkpoints, exporters, settings, input_path, want_bundle, emit)
            raise exc

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

    def resume(
        self,
        input_path: Path,
        settings: QAOpsSettings,
        *,
        from_: str | None = None,
        formats: Sequence[str] | None = None,
        report: ProgressReporter | None = None,
        events: EventSink | None = None,
    ) -> DesignOutcome:
        """Resume a failed run from its last successful checkpoint (ADR-040).

        Reads checkpoints under settings.output_dir, rehydrates the furthest
        completed stage's output, and runs only the remaining stages - completed
        stages are reused, never re-run. If no checkpoint exists, this falls back
        to a normal full run. In-process resume only: it does not reconstruct
        state across a server restart (out of scope for Phase 25).
        """
        emit = report or (lambda _line: None)
        emit_event = events or (lambda _event: None)

        # Phase 36B Part 2: same image-evidence reconstruction as run(). Only the
        # analyzer (stage 1) consumes it; when resuming past the analyzer it has no
        # effect, but loading it keeps a resume-from-start consistent with a fresh
        # run and preserves the fail-clearly contract on a corrupt sidecar.
        evidence = load_evidence_package(settings.output_dir.parent)

        entry_point, detection = self.resolve_entry_point(input_path, from_)
        export_formats = list(formats) if formats else list(settings.default_export_formats)
        want_bundle = CsvBundleExporter.format_name in export_formats
        file_formats = [f for f in export_formats if f != CsvBundleExporter.format_name]
        exporters = resolve_exporters(file_formats)

        checkpoints = CheckpointStore(settings.output_dir)
        checkpoint = checkpoints.latest_checkpoint()
        if checkpoint is None:
            emit("No checkpoint found; starting a fresh run.")
            return self.run(
                input_path,
                settings,
                from_=from_,
                formats=formats,
                report=report,
                events=events,
            )

        stage_names = stage_names_for(entry_point)
        # The checkpoint's stage_index is absolute (full pipeline). Translate to
        # this entry point's stage list to find where to resume.
        completed_names = set(checkpoints.completed_stages())
        resume_index = 0
        for i, name in enumerate(stage_names):
            if name in completed_names:
                resume_index = i + 1
        if resume_index >= len(stage_names):
            emit("All stages already completed; re-exporting from checkpoint.")
            partial = _promote_to_partial_result(checkpoint.result, input_path.stem)
            result = partial if isinstance(partial, TestDesignResult) else None
            if result is not None:
                artifacts = self._write_reports(
                    result,
                    exporters,
                    settings,
                    input_path,
                    want_bundle=want_bundle,
                    emit=emit,
                )
                return DesignOutcome(
                    result=result,
                    entry_point=entry_point,
                    detection=detection,
                    artifacts=artifacts,
                )

        emit(
            f"Resuming '{input_path.name}' from stage {resume_index} "
            f"({stage_names[resume_index] if resume_index < len(stage_names) else 'end'})"
        )

        def _checkpoint(stage_name: str, stage_index: int, output: BaseModel) -> None:
            checkpoints.write_stage(stage_name, stage_index, output)

        try:
            result = self._execute(
                entry_point,
                checkpoint.result,
                settings,
                emit,
                emit_event,
                checkpoint=_checkpoint,
                start_index=resume_index,
                evidence=evidence,
            )
        except StageError as exc:
            self._export_partial(checkpoints, exporters, settings, input_path, want_bundle, emit)
            raise exc

        artifacts = self._write_reports(
            result, exporters, settings, input_path, want_bundle=want_bundle, emit=emit
        )
        return DesignOutcome(
            result=result,
            entry_point=entry_point,
            detection=detection,
            artifacts=artifacts,
        )

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
        checkpoint: CheckpointSink | None = None,
        start_index: int = 0,
        evidence: EvidencePackage | None = None,
    ) -> TestDesignResult:
        """Run through the adaptive executor (single- or multi-provider).

        `evidence` (Phase 36B) is optional image evidence bound to the analyzer only
        (stage 1). It defaults to None, so runs without images build a byte-identical
        pipeline.
        """
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
                evidence=evidence,
            )
            return list(built.stages)

        has_images = evidence is not None and evidence.has_images
        executor = AdaptiveExecutor(
            candidates,
            settings,
            build_stages,
            registry=self._registry,
            reporter=emit,
            events=emit_event,
            checkpoint=checkpoint,
            start_index=start_index,
            # Phase 40B: name the single image-consuming stage (the requirement
            # analyzer) only when this run carries images. The executor requires an
            # image-capable provider for that stage and excludes the image provider
            # for every downstream stage. Text runs pass None -> unchanged behavior.
            image_stage_name="requirement_analyzer" if has_images else None,
            stage_names=tuple(stage_names_for(entry_point)),
        )
        result = executor.run(pipeline_input)

        if not isinstance(result, TestDesignResult):  # pragma: no cover - defensive
            msg = "Pipeline did not produce a TestDesignResult"
            raise ConfigurationError(msg)
        return result

    def _export_partial(
        self,
        checkpoints: CheckpointStore,
        exporters: list[ExporterInstance],
        settings: QAOpsSettings,
        input_path: Path,
        want_bundle: bool,
        emit: ProgressReporter,
    ) -> list[DesignArtifact]:
        """Export whatever the furthest checkpoint produced (ADR-040).

        Called when a stage fails. Promotes the latest checkpoint (a cumulative
        snapshot) to a partial TestDesignResult and writes the CSV-bundle files
        for the dimensions that have data; absent dimensions are simply not
        written. Best-effort: any export error here is swallowed so it cannot
        mask the original StageError.
        """
        try:
            checkpoint = checkpoints.latest_checkpoint()
        except Exception:  # noqa: BLE001 - corrupt checkpoint must not mask failure
            emit("No usable checkpoint to export partial artifacts from.")
            return []
        if checkpoint is None:
            return []
        partial = _promote_to_partial_result(checkpoint.result, input_path.stem)
        if partial is None:
            return []
        emit(f"Exporting partial artifacts from '{checkpoint.stage_name}' checkpoint")
        artifacts: list[DesignArtifact] = []
        out_dir = settings.output_dir
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            # The CSV bundle writes one file per dimension and naturally emits
            # empty files for absent dimensions; write it for partial results.
            bundle_paths = CsvBundleExporter().export_bundle(partial, out_dir)
            for path in bundle_paths:
                p = Path(path)
                artifacts.append(DesignArtifact(name=p.name, format="CSV (partial)", path=p))
            # Also emit the canonical partial JSON so the full snapshot is
            # downloadable.
            for exporter in exporters:
                if exporter.format_name.lower() == "json":
                    stem = input_path.stem
                    target = out_dir / f"{stem}.partial{exporter.file_extension}"
                    written = exporter.export(partial, str(target))
                    wp = Path(written)
                    artifacts.append(DesignArtifact(name=wp.name, format="JSON (partial)", path=wp))
        except (OSError, ExportError):  # pragma: no cover - best effort
            emit("Partial export encountered a filesystem error; skipping.")
        return artifacts

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


def _promote_to_partial_result(model: BaseModel, source_name: str) -> TestDesignResult | None:
    """Build a partial TestDesignResult from any checkpoint model (ADR-040).

    Stage outputs are cumulative and nested, so every checkpoint type carries a
    prefix of the final result's dimensions. We copy whatever is present and
    leave the rest at their empty defaults; only source_name is required.
    A model we do not recognise yields None (nothing to export).
    """
    if isinstance(model, TestDesignResult):
        return model
    if isinstance(model, ConditionDesignResult):
        analysis = model.scenario_design.analysis
        return TestDesignResult(
            source_name=analysis.source_name or source_name,
            requirements=list(analysis.requirements),
            business_rules=list(analysis.business_rules),
            gap_report=analysis.gap_report,
            scenarios=list(model.scenario_design.scenarios),
            conditions=list(model.conditions),
            expansion_truncated=model.expansion_truncated,
            truncation_note=model.truncation_note,
        )
    if isinstance(model, ScenarioDesignResult):
        analysis = model.analysis
        return TestDesignResult(
            source_name=analysis.source_name or source_name,
            requirements=list(analysis.requirements),
            business_rules=list(analysis.business_rules),
            gap_report=analysis.gap_report,
            scenarios=list(model.scenarios),
        )
    if isinstance(model, RequirementAnalysisResult):
        return TestDesignResult(
            source_name=model.source_name or source_name,
            requirements=list(model.requirements),
            business_rules=list(model.business_rules),
            gap_report=model.gap_report,
        )
    return None


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
        "test_conditions": metrics.total_conditions,
        "test_cases": metrics.total_test_cases,
        "gaps": len(result.gap_report.gaps),
        # Kept for backward compatibility: a single headline percent equal to
        # requirement coverage. It is NOT a claim of exhaustive testing; the
        # per-dimension figures below tell the fuller story (ADR-036).
        "coverage_percent": metrics.requirement_coverage_pct,
        "requirement_coverage_percent": metrics.requirement_coverage_pct,
        "business_rule_coverage_percent": metrics.business_rule_coverage_pct,
        "scenario_coverage_percent": metrics.scenario_coverage_pct,
        "condition_coverage_percent": metrics.condition_coverage_pct,
        "unresolved_conditions": metrics.unresolved_conditions,
        "expansion_truncated": int(metrics.expansion_truncated),
    }
