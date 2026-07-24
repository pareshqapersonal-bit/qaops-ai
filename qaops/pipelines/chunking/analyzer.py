"""ChunkedRequirementAnalyzer - chunking as an internal capability (ADR-020).

A drop-in replacement for RequirementAnalyzer at pipeline position 0: same
`run(RequirementInput) -> RequirementAnalysisResult` signature, so no
downstream stage, model, exporter, or prompt changes. Internally it plans
chunks, runs the *existing, unmodified* analyzer on each, and merges the
results into one canonical analysis with fresh REQ IDs.

Documents that fit within chunk_size take the single-chunk path and delegate
straight to the wrapped analyzer, so small-document behavior is unchanged.

The input-size guardrail (ADR-006) is deliberately checked here against the
whole document, before splitting: chunking is about output volume, not a
licence to feed unbounded input.
"""

from qaops.config import QAOpsSettings
from qaops.core.errors import InputTooLargeError, StageError
from qaops.llm import LLMClient, PromptLoader
from qaops.models import RequirementAnalysisResult, RequirementInput
from qaops.pipelines.chunking.merge import merge_requirements
from qaops.pipelines.chunking.planner import ChunkPlanner
from qaops.pipelines.chunking.strategy import ChunkStrategy


def _configured_model(settings: QAOpsSettings) -> str:
    """The model string for the active provider."""
    if settings.provider == "gemini":
        return settings.gemini_model
    if settings.provider == "openrouter":
        return settings.openrouter_model
    return settings.model


class ChunkedRequirementAnalyzer:
    """Runs the existing analyzer over planned chunks and merges the results."""

    # Deliberately the same stage name as RequirementAnalyzer: chunking is an
    # internal implementation detail, and the stage name is observable in error
    # messages and pipeline introspection. Renaming it would leak the detail
    # and break callers that identify the stage by name.
    name = "requirement_analyzer"

    def __init__(self, client: LLMClient, prompts: PromptLoader, settings: QAOpsSettings) -> None:
        # Imported here rather than at module scope: the test_design package
        # imports this module for pipeline composition, so a top-level import
        # of its analyzer would be circular.
        from qaops.pipelines.test_design.analyzer import RequirementAnalyzer

        self._settings = settings
        self._analyzer = RequirementAnalyzer(client, prompts, settings)
        fixed = settings.chunking_strategy == "fixed"
        self._strategy = ChunkStrategy(
            provider=settings.provider,
            model=_configured_model(settings),
            safety_margin=settings.chunk_safety_margin,
            fixed_chunk_size=settings.chunk_size if fixed else None,
            fixed_chunk_overlap=settings.chunk_overlap if fixed else None,
        )

    def run(self, data: RequirementInput) -> RequirementAnalysisResult:
        if len(data.text) > self._settings.max_input_chars:
            msg = (
                f"Input '{data.source_name}' is {len(data.text)} characters; "
                f"the configured limit is {self._settings.max_input_chars} "
                "(QAOPS_MAX_INPUT_CHARS). Split the document and analyze the "
                "parts separately."
            )
            raise InputTooLargeError(msg)

        decision = self._strategy.decide(len(data.text))
        if not decision.should_chunk:
            # Bypass: the analyzer executes exactly as it would with no
            # chunking involved.
            return self._analyzer.run(data)

        planner = ChunkPlanner(
            chunk_size=decision.chunk_size,
            chunk_overlap=decision.chunk_overlap,
        )
        chunks = planner.plan(data.text)
        if len(chunks) <= 1:
            return self._analyzer.run(data)

        results: list[RequirementAnalysisResult] = []
        failures: list[str] = []
        for chunk in chunks:
            chunk_input = RequirementInput(
                text=chunk.text,
                source_name=f"{data.source_name} [chunk {chunk.index}/{chunk.total}]",
            )
            try:
                results.append(self._analyzer.run(chunk_input))
            except StageError as exc:
                # A chunk with no requirements (e.g. a table of contents) is
                # normal in a long document and must not fail the whole run.
                # Record it and continue; only a total failure is fatal.
                failures.append(f"chunk {chunk.index}/{chunk.total}: {exc}")

        if not results:
            raise StageError(
                self.name,
                f"No requirements extracted from any of {len(chunks)} chunks of "
                f"'{data.source_name}'. Details: {'; '.join(failures)}",
            )

        return merge_requirements(
            results,
            source_name=data.source_name,
            source_text=data.text,
        )
