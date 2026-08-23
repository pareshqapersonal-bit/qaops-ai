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
import re
from typing import TYPE_CHECKING

from qaops.clarification.agent import ClarificationAgent
from qaops.clarification.enums import ClarificationStatus, QuestionStatus
from qaops.clarification.gap_diff import diff_gaps, gap_signature
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
from qaops.models.domain import GapReport, RequirementAnalysisResult, RequirementInput
from qaops.models.enums import GapSeverity
from qaops.pipelines.chunking.analyzer import ChunkedRequirementAnalyzer
from qaops.pipelines.test_design.gaps import GapAnalyzer

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from qaops.clarification.models import ClarificationAnswer, ClarificationQuestion
    from qaops.config import QAOpsSettings
    from qaops.models.domain import Requirement

# Decision 8(6): at most 5 clarification rounds before proceed-with-assumptions is
# required/allowed. The cap is a safety valve against a never-ready loop.
MAX_CLARIFICATION_ROUNDS = 5

# Filename for the analyzed requirements persisted during clarification start, so the
# handoff (41C-2) can apply answers and feed them to the `requirements` entry point
# without re-running the analyzer or gap stages.
_REQUIREMENTS_ARTIFACT = "analyzed_requirements.json"
_CLARIFIED_REQUIREMENTS = "clarified_requirements.json"
_SOURCE_TEXT_ARTIFACT = "analyzed_source_text.txt"


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
                # Gap analysis is text-only (no image payload), so it expresses only
                # its real requirements. Capability-driven: image-capable providers
                # are NOT excluded - a multimodal provider may serve this stage if
                # the existing chain/order selects it.
            ),
            run_call=lambda client: GapAnalyzer(client, prompts, settings).run(analyzed),
        )
        return list(analyzed.requirements), analyzed.gap_report, analyzed.source_text

    def _load_source_text(self, workspace: Path) -> str:
        """Load the persisted analyzed source text for the iterative gap re-run.

        Written by start(); absent only for runs created before 41E-3, in which
        case an empty string is used (gap analysis still runs on the augmented
        requirements, which carry the meaningful signal).
        """
        path = workspace / _SOURCE_TEXT_ARTIFACT
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def _rerun_gap_analysis(
        self,
        requirements: Sequence[Requirement],
        source_text: str,
        settings: QAOpsSettings,
    ) -> GapReport:
        """Re-run ONLY the gap stage on (augmented) requirements - text-only.

        Used by the iterative loop (41E-3): after answers augment the requirements,
        gaps are recomputed to discover whether new meaningful gaps remain. This is
        a plain text request: it does NOT set needs_images (requirements already
        exist; no image analysis) and does NOT exclude image-capable providers, so
        the normal text-provider chain is used AND an image-capable provider such as
        NVIDIA remains available as a fallback. This matters when the deployment's
        text providers are unavailable and NVIDIA is the only reachable one -
        excluding it would leave no candidate and fail the whole answer round.
        The analyzer is NOT re-run - requirement IDs/descriptions are preserved
        (only assumptions are appended by augmentation). Phase 40B downstream-stage
        image exclusion in the normal pipeline is unaffected by this.
        """
        prompts = PromptLoader(version=settings.prompt_version)
        analyzed = RequirementAnalysisResult(
            source_name="clarified-requirements",
            source_text=source_text,
            requirements=list(requirements),
            gap_report=GapReport(gaps=[]),
        )
        result = resilient_structured_call(
            settings=settings,
            requirements=StageRequirements(
                needs_structured_output=True,
                free_only=_is_free_only(settings),
            ),
            run_call=lambda client: GapAnalyzer(client, prompts, settings).run(analyzed),
        )
        return result.gap_report

    def _generate_questions(
        self,
        requirements: Sequence[Requirement],
        gap_report: GapReport,
        settings: QAOpsSettings,
    ) -> list[ClarificationQuestion]:
        """Generate a clarification question batch from a GapReport (text-only)."""
        return resilient_structured_call(
            settings=settings,
            requirements=StageRequirements(
                needs_structured_output=True,
                free_only=_is_free_only(settings),
            ),
            run_call=lambda client: ClarificationAgent(
                client, PromptLoader(version=settings.prompt_version), settings
            ).generate_questions(list(requirements), gap_report),
        )

    # -- start clarification --------------------------------------------------

    def start(self, run_id: str, input_path: Path, workspace: Path) -> ClarificationState:
        """Run bounded analysis, generate the first question batch, persist state."""
        settings = self._settings.model_copy(update={"output_dir": workspace / "output"})
        requirements, gap_report, source_text = self._analyze(input_path, settings)

        # Persist the analyzed requirements so the 41C-2 handoff can apply answers and
        # feed them to the `requirements` entry point without re-running the analyzer.
        _write_requirements_artifact(workspace / _REQUIREMENTS_ARTIFACT, requirements)
        # Persist the source text so the 41E-3 iterative loop can re-run gap analysis
        # on augmented requirements without re-running the (possibly image) analyzer.
        (workspace / _SOURCE_TEXT_ARTIFACT).write_text(source_text, encoding="utf-8")

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
            asked_gap_signatures=_signatures_for_questions(questions),
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

        settings = self._settings.model_copy(update={"output_dir": workspace / "output"})

        # Augment the persisted requirements with the answers (preserves IDs and
        # descriptions - apply_answers only appends assumptions). Requirements are
        # reloaded from the artifact written at start(); if absent (older run), fall
        # back to an empty list so status/assumption tracking still works.
        artifact = workspace / _REQUIREMENTS_ARTIFACT
        base_requirements = _load_requirements_artifact(artifact) if artifact.exists() else []
        agent = ClarificationAgent.for_answer_processing()
        augmented_requirements, updated_questions = agent.apply_answers(
            base_requirements, state.questions, answers
        )

        assumptions = list(state.assumptions)
        asked_signatures = list(state.asked_gap_signatures)
        # Merge answers latest-wins by question_id so a retry (after a failed LLM
        # round persisted the checkpoint below) re-submitting the same answers
        # replaces rather than duplicates them, keeping state.answers idempotent.
        all_answers = _merge_answers(state.answers, answers)

        if proceed_with_assumptions:
            # User accepts unresolved gaps: mark remaining questions skipped (their
            # gaps become accepted assumptions) and go ready. No re-analysis.
            updated_questions, new_assumptions = agent.mark_skipped(updated_questions)
            assumptions.extend(new_assumptions)
            readiness = compute_readiness(
                updated_questions,
                critical_gaps=0,
                requirements_total=state.readiness.requirements_total,
            )
            status = ClarificationStatus.READY_FOR_TEST_DESIGN
            new_iteration = state.iteration
        else:
            # Persist-first durability fix: the iterative loop below makes LLM calls
            # (gap re-run + question generation) that can be slow or fail. Write the
            # merged answers and their answered-question statuses to state BEFORE any
            # LLM work, so a timeout/provider failure never loses the user's answers
            # and the run stays safely retryable (status left CLARIFYING). This
            # checkpoint carries the same answers/questions the final write will,
            # minus the not-yet-computed new batch/readiness, so a retry re-applies
            # answers idempotently rather than duplicating them.
            checkpoint = state.model_copy(
                update={
                    "questions": updated_questions,
                    "answers": all_answers,
                    "assumptions": assumptions,
                    "status": ClarificationStatus.CLARIFYING,
                }
            )
            write_clarification_state(workspace, checkpoint)

            # Iterative loop (41E-3): re-run gap analysis on the augmented
            # requirements, classify against history, and generate a NEW question
            # batch only for genuinely new gaps (never re-asking asked/accepted ones).
            source_text = self._load_source_text(workspace)
            gap_report = self._rerun_gap_analysis(augmented_requirements, source_text, settings)
            diff = diff_gaps(
                gap_report,
                asked_signatures=asked_signatures,
                accepted_signatures=_accepted_signatures(updated_questions),
            )
            new_gaps = [c.gap for c in diff.new]

            new_questions: list[ClarificationQuestion] = []
            if new_gaps:
                new_questions = self._generate_questions(
                    augmented_requirements,
                    GapReport(gaps=new_gaps),
                    settings,
                )
                # The agent numbers each batch from Q-001; re-scope the new batch's
                # ids to be unique across the whole run before merging, so no two
                # questions share a question_id (which the frontend keys answers and
                # radio groups on). Existing ids are unchanged; signatures are
                # derived from gap_reference (not the id), so this does not affect
                # duplicate-gap suppression.
                new_questions = _reindex_new_questions(updated_questions, new_questions)
                asked_signatures.extend(_signatures_for_questions(new_questions))

            # Merge: keep the answered/skipped prior questions, append the new batch.
            updated_questions = [*updated_questions, *new_questions]

            readiness = compute_readiness(
                updated_questions,
                critical_gaps=_unanswered_blocking(updated_questions),
                requirements_total=state.readiness.requirements_total,
            )
            if readiness.ready:
                status = ClarificationStatus.READY_FOR_TEST_DESIGN
                new_iteration = state.iteration
            else:
                if state.iteration >= MAX_CLARIFICATION_ROUNDS:
                    raise ClarificationRoundLimitError(
                        f"Reached {MAX_CLARIFICATION_ROUNDS} clarification rounds; "
                        "resubmit with proceed_with_assumptions=true to continue."
                    )
                # New meaningful gaps -> another round; otherwise stay clarifying.
                status = (
                    ClarificationStatus.RE_ANALYZING
                    if new_questions
                    else ClarificationStatus.CLARIFYING
                )
                new_iteration = state.iteration + 1

        new_state = state.model_copy(
            update={
                "questions": updated_questions,
                "answers": all_answers,
                "assumptions": assumptions,
                "readiness": readiness,
                "status": status,
                "iteration": new_iteration,
                "asked_gap_signatures": asked_signatures,
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

        # Record the explicit user Proceed decision persistently and traceably
        # (41E-4). This is a clarification-state marker only; READY_FOR_TEST_DESIGN
        # remains the authoritative run-lifecycle state. Idempotent: re-running the
        # handoff re-persists PROCEEDED with no other effect.
        if state.status is not ClarificationStatus.PROCEEDED:
            write_clarification_state(
                workspace,
                state.model_copy(update={"status": ClarificationStatus.PROCEEDED}),
            )
        return target


def _is_free_only(settings: QAOpsSettings) -> bool:
    """Whether the free_only execution strategy is active (mirrors the executor)."""
    return settings.execution_strategy == "free_only"


def _signatures_for_questions(
    questions: Sequence[ClarificationQuestion],
) -> list[str]:
    """Gap signatures for a batch of questions (requirement_id + gap description).

    A question's gap_reference is the originating gap's description (agent sets
    gap_reference=gap.description), so the same signature the 41E-2 layer computes
    from a Gap can be reconstructed from a persisted question - no extra state.
    """
    return [gap_signature(q.requirement_id, q.gap_reference) for q in questions]


def _accepted_signatures(
    questions: Sequence[ClarificationQuestion],
) -> list[str]:
    """Signatures of questions accepted as assumptions (status SKIPPED).

    Skipped questions became assumptions (proceed-with-assumptions or explicit
    skip); their gaps must never be re-asked, so they are fed to diff_gaps as
    accepted_signatures and can never be reclassified NEW.
    """
    return [
        gap_signature(q.requirement_id, q.gap_reference)
        for q in questions
        if q.status is QuestionStatus.SKIPPED
    ]


def _merge_answers(
    existing: Sequence[ClarificationAnswer],
    incoming: Sequence[ClarificationAnswer],
) -> list[ClarificationAnswer]:
    """Merge answer batches latest-wins by question_id, preserving first-seen order.

    Keeps state.answers idempotent across retries: if the persist-first checkpoint
    saved a batch and the user resubmits the same questions (e.g. after an LLM
    failure), the resubmission replaces the prior answer for that question instead
    of appending a duplicate. Order follows first appearance so the answer list
    stays stable/deterministic.
    """
    merged: dict[str, ClarificationAnswer] = {}
    for a in (*existing, *incoming):
        merged[a.question_id] = a  # latest wins
    return list(merged.values())


# Matches the "Q-###" id convention (e.g. Q-001). Non-matching ids are left alone.
_Q_ID_RE = re.compile(r"^Q-(\d+)$")


def _reindex_new_questions(
    existing: Sequence[ClarificationQuestion],
    new_questions: Sequence[ClarificationQuestion],
) -> list[ClarificationQuestion]:
    """Give a newly generated batch globally-unique ``Q-###`` ids for the run.

    The agent numbers every batch from Q-001, so merging a new round's batch onto
    prior questions (41E-3) produces duplicate ids. This continues numbering from
    the highest existing numeric id, so round 2 -> Q-004.. after round 1's Q-003,
    etc. Existing question ids are NEVER changed; only the new batch is renumbered,
    preserving each new question's order, content, priority, answer_type, options,
    status, and gap_reference (only ``question_id`` changes). Any existing id that
    does not match ``Q-###`` is ignored when computing the max (its value is left
    untouched); a still-colliding new id is bumped past the running counter so the
    result is always collision-free.
    """
    existing_ids = {q.question_id for q in existing}
    highest = 0
    for q in existing:
        match = _Q_ID_RE.match(q.question_id)
        if match:
            highest = max(highest, int(match.group(1)))

    reindexed: list[ClarificationQuestion] = []
    counter = highest
    for q in new_questions:
        counter += 1
        candidate = f"Q-{counter:03d}"
        # Defensive: skip past any residual collision with a non-Q-### existing id.
        while candidate in existing_ids:
            counter += 1
            candidate = f"Q-{counter:03d}"
        existing_ids.add(candidate)
        reindexed.append(q.model_copy(update={"question_id": candidate}))
    return reindexed


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
