"""Clarification service (Phase 41C-1).

Composes the clarification workflow ABOVE the existing pipeline, touching no
protected stage/executor code. It:

1. runs a BOUNDED analysis - the existing RequirementAnalyzer + GapAnalyzer stages,
   constructed directly (option 8(a)), evidence bound to the analyzer only (40B) -
   to produce requirements + a GapReport;
2. asks the Phase 41B ClarificationAgent to turn the GapReport into a question batch;
3. persists the Phase 41A ClarificationState to the run workspace;
4. applies submitted answers (augmenting requirements) and recomputes readiness,
   iterating until blocking gaps clear or the round cap is reached.

It does NOT modify DesignService, the executor/selector, GapAnalyzer, structured.py,
providers, or image ingestion. The final handoff to the test-design pipeline is 41C-2
and is intentionally absent here.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from qaops.clarification.agent import ClarificationAgent
from qaops.clarification.enums import ClarificationStatus, QuestionStatus
from qaops.clarification.models import ClarificationState
from qaops.clarification.readiness import compute_readiness
from qaops.clarification.state_store import (
    load_clarification_state,
    write_clarification_state,
)
from qaops.execution.resilient_call import resilient_structured_call
from qaops.execution.selector import StageRequirements
from qaops.ingestion.evidence_sidecar import load_evidence_package
from qaops.ingestion.registry import load_document
from qaops.llm.prompt_loader import PromptLoader
from qaops.models.domain import RequirementInput
from qaops.models.enums import GapSeverity
from qaops.pipelines.chunking.analyzer import ChunkedRequirementAnalyzer
from qaops.pipelines.test_design.gaps import GapAnalyzer

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from qaops.clarification.models import ClarificationAnswer, ClarificationQuestion
    from qaops.config import QAOpsSettings
    from qaops.models.domain import GapReport, Requirement

# Decision 8(6): at most 5 clarification rounds before proceed-with-assumptions is
# required/allowed. The cap is a safety valve against a never-ready loop.
MAX_CLARIFICATION_ROUNDS = 5

# Filename for the analyzed requirements persisted during clarification start, so the
# handoff (41C-2) can apply answers and feed them to the `requirements` entry point
# without re-running the analyzer or gap stages.
_REQUIREMENTS_ARTIFACT = "analyzed_requirements.json"
_CLARIFIED_REQUIREMENTS = "clarified_requirements.json"


class ClarificationNotFoundError(RuntimeError):
    """No clarification state exists for the run (e.g. a one-shot run)."""


class ClarificationRoundLimitError(RuntimeError):
    """The clarification round cap was reached; proceed-with-assumptions required."""


class ClarificationNotReadyError(RuntimeError):
    """Test-design handoff requested before the run reached readiness."""


class ClarificationService:
    """Runs bounded analysis + clarification for a run workspace."""

    def __init__(self, settings: QAOpsSettings) -> None:
        self._settings = settings

    # -- bounded analysis (option 8(a): compose existing stages directly) -----

    def _analyze(
        self, input_path: Path, settings: QAOpsSettings
    ) -> tuple[list[Requirement], GapReport, str]:
        """Run the existing analyzer + gap stages only, returning their outputs.

        Reuses the exact construction the pipeline builder uses: the analyzer is
        bound to image evidence (40B - only stage 1 sees images), gap analysis is
        text-only. No downstream stage runs; nothing here modifies those stages.

        Each call runs through resilient_structured_call (Phase 41C-4), which gives
        the clarification path the same policy-driven provider failover the executor
        applies per stage: a transient NVIDIA 500 (NEXT_MODEL) fails over to the next
        eligible provider instead of aborting the run. The helper builds a FRESH
        client per attempt, preserving the Phase 41C-3 event-loop fix. The analyzer
        requires an image-capable provider only when the run actually carries image
        evidence; gap analysis is always text-only and may fail over to Groq/Gemini/
        OpenRouter per the existing strategy.
        """
        source_text = load_document(input_path)
        prompts = PromptLoader(version=settings.prompt_version)
        # Evidence lives beside the output dir (same location DesignService reads).
        evidence = load_evidence_package(settings.output_dir.parent)
        has_images = bool(evidence and evidence.images)

        analyzed = resilient_structured_call(
            settings=settings,
            requirements=StageRequirements(
                needs_structured_output=True,
                free_only=_is_free_only(settings),
                needs_images=has_images,
            ),
            run_call=lambda client: ChunkedRequirementAnalyzer(
                client, prompts, settings, evidence=evidence
            ).run(RequirementInput(text=source_text, source_name=input_path.name)),
        )
        analyzed = resilient_structured_call(
            settings=settings,
            requirements=StageRequirements(
                needs_structured_output=True,
                free_only=_is_free_only(settings),
                # Gap analysis is text-only; on an image run it must exclude the
                # image provider downstream, matching Phase 40B.
                exclude_image_providers=has_images,
            ),
            run_call=lambda client: GapAnalyzer(client, prompts, settings).run(analyzed),
        )
        return list(analyzed.requirements), analyzed.gap_report, analyzed.source_text

    # -- start clarification --------------------------------------------------

    def start(self, run_id: str, input_path: Path, workspace: Path) -> ClarificationState:
        """Run bounded analysis, generate the first question batch, persist state."""
        settings = self._settings.model_copy(update={"output_dir": workspace / "output"})
        requirements, gap_report, _ = self._analyze(input_path, settings)

        # Persist the analyzed requirements so the 41C-2 handoff can apply answers and
        # feed them to the `requirements` entry point without re-running the analyzer.
        _write_requirements_artifact(workspace / _REQUIREMENTS_ARTIFACT, requirements)

        # The agent's question-generation call is text-only and also runs through
        # the resilient helper: a fresh agent (hence fresh client) per attempt, with
        # the same provider failover. It never carries images (41B invariant).
        questions = resilient_structured_call(
            settings=settings,
            requirements=StageRequirements(
                needs_structured_output=True,
                free_only=_is_free_only(settings),
            ),
            run_call=lambda client: ClarificationAgent(
                client, PromptLoader(version=settings.prompt_version), settings
            ).generate_questions(requirements, gap_report),
        )

        critical_gaps = _count_blocker_gaps(gap_report)
        readiness = compute_readiness(
            questions, critical_gaps=critical_gaps, requirements_total=len(requirements)
        )
        status = (
            ClarificationStatus.READY_FOR_TEST_DESIGN
            if readiness.ready
            else ClarificationStatus.CLARIFYING
        )
        state = ClarificationState(
            run_id=run_id,
            iteration=1,
            status=status,
            questions=questions,
            readiness=readiness,
        )
        write_clarification_state(workspace, state)
        return state

    # -- submit answers -------------------------------------------------------

    def submit_answers(
        self,
        workspace: Path,
        answers: Sequence[ClarificationAnswer],
        *,
        proceed_with_assumptions: bool = False,
    ) -> ClarificationState:
        """Apply answers, recompute readiness, and persist the updated state.

        Loads the current 41A state; applies answers via the agent (augmenting the
        questions' answered status); if the user chooses to proceed, marks the
        remaining questions skipped and records assumptions. Readiness is recomputed
        via the existing compute_readiness. Raises if no state exists or the round
        cap is hit without proceeding.
        """
        state = load_clarification_state(workspace)
        if state is None:
            raise ClarificationNotFoundError("No clarification in progress for this run.")

        # Apply answers to the persisted questions (agent handles contradictions).
        # Requirement augmentation itself lands in 41C-2 at handoff; here we track
        # answered/skipped status and assumptions on the state. apply_answers and
        # mark_skipped are pure (no LLM), so no client is created for this path.
        agent = ClarificationAgent.for_answer_processing()
        _, updated_questions = agent.apply_answers([], state.questions, answers)

        assumptions = list(state.assumptions)
        status = ClarificationStatus.CLARIFYING
        if proceed_with_assumptions:
            updated_questions, new_assumptions = agent.mark_skipped(updated_questions)
            assumptions.extend(new_assumptions)

        readiness = compute_readiness(
            updated_questions,
            critical_gaps=0
            if proceed_with_assumptions
            else _unanswered_blocking(updated_questions),
            requirements_total=state.readiness.requirements_total,
        )
        if readiness.ready:
            status = ClarificationStatus.READY_FOR_TEST_DESIGN
        elif state.iteration >= MAX_CLARIFICATION_ROUNDS and not proceed_with_assumptions:
            raise ClarificationRoundLimitError(
                f"Reached {MAX_CLARIFICATION_ROUNDS} clarification rounds; "
                "resubmit with proceed_with_assumptions=true to continue."
            )

        new_state = state.model_copy(
            update={
                "questions": updated_questions,
                "answers": [*state.answers, *answers],
                "assumptions": assumptions,
                "readiness": readiness,
                "status": status,
                "iteration": state.iteration + (0 if readiness.ready else 1),
            }
        )
        write_clarification_state(workspace, new_state)
        return new_state

    # -- handoff to test design (41C-2) ---------------------------------------

    def prepare_test_design(self, workspace: Path) -> Path:
        """Produce the clarified-requirements JSON for the test-design handoff.

        Loads the analyzed requirements persisted at start and the current
        clarification state, applies the answers by AUGMENTING requirements (via the
        41B agent - originals and order preserved so re-parsed IDs stay stable), and
        writes clarified_requirements.json. Returns its path. Raises if the run is not
        ready or the analyzed-requirements artifact is missing. Idempotent: re-running
        overwrites the same file, so a duplicate handoff is safe.
        """
        state = load_clarification_state(workspace)
        if state is None:
            raise ClarificationNotFoundError("No clarification in progress for this run.")
        if not state.readiness.ready:
            raise ClarificationNotReadyError(
                "Clarification is not ready for test design; blocking questions remain."
            )
        artifact = workspace / _REQUIREMENTS_ARTIFACT
        if not artifact.exists():
            raise ClarificationNotFoundError(
                "Analyzed requirements artifact is missing; cannot hand off."
            )

        requirements = _load_requirements_artifact(artifact)
        agent = ClarificationAgent.for_answer_processing()
        clarified, _ = agent.apply_answers(requirements, state.questions, list(state.answers))

        target = workspace / _CLARIFIED_REQUIREMENTS
        _write_requirements_artifact(target, clarified)
        return target


def _is_free_only(settings: QAOpsSettings) -> bool:
    """Whether the free_only execution strategy is active (mirrors the executor)."""
    return settings.execution_strategy == "free_only"


def _count_blocker_gaps(gap_report: GapReport) -> int:
    return sum(1 for g in gap_report.gaps if g.severity is GapSeverity.BLOCKER)


def _write_requirements_artifact(path: Path, requirements: Sequence[Requirement]) -> None:
    """Serialize requirements to the JSON shape parse_requirements accepts.

    Written under a "requirements" key so the file can be fed straight into the
    `requirements` entry point. Order is preserved so re-parsed IDs stay stable.
    """
    payload = {"requirements": [r.model_dump(mode="json") for r in requirements]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _load_requirements_artifact(path: Path) -> list[Requirement]:
    """Reconstruct Requirement objects from the persisted analyzed-requirements JSON."""
    from qaops.models.domain import Requirement

    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Requirement.model_validate(r) for r in raw.get("requirements", [])]


def _unanswered_blocking(questions: Sequence[ClarificationQuestion]) -> int:
    from qaops.clarification.enums import QuestionPriority

    return sum(
        1
        for q in questions
        if q.priority is QuestionPriority.BLOCKING and q.status is QuestionStatus.UNANSWERED
    )
