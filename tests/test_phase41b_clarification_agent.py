"""Phase 41B tests: ClarificationAgent (question generation + answer processing).

Covers gap->question mapping with severity->priority, answer-type selection,
duplicate/skip suppression, answer application by requirement augmentation (not
regeneration), assumption creation, readiness via the existing compute_readiness,
multi-iteration, contradictions, and malformed LLM responses. All LLM calls are
mocked; no live provider calls and no images are ever passed to the agent.
"""

import json
from pathlib import Path

import pytest

from qaops.clarification import (
    AnswerType,
    ClarificationAgent,
    ClarificationAnswer,
    ClarificationState,
    QuestionPriority,
    QuestionStatus,
)
from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.llm import MockLLMClient, PromptLoader
from qaops.models.domain import Gap, GapReport, Requirement
from qaops.models.enums import GapSeverity


@pytest.fixture
def settings(tmp_path: Path) -> QAOpsSettings:
    return QAOpsSettings(output_dir=tmp_path / "out")


@pytest.fixture
def prompts() -> PromptLoader:
    return PromptLoader()


@pytest.fixture
def requirements() -> list[Requirement]:
    return [
        Requirement(
            id="REQ-001",
            title="Store availability",
            description="User checks store availability by pincode.",
            source_excerpt="check store availability",
        ),
        Requirement(
            id="REQ-002",
            title="Lead generation",
            description="System generates a Salesforce lead.",
            source_excerpt="generate lead",
        ),
    ]


def _agent(settings: QAOpsSettings, prompts: PromptLoader, response: str) -> ClarificationAgent:
    return ClarificationAgent(MockLLMClient([response]), prompts, settings)


def _batch(*questions: dict) -> str:
    return json.dumps({"questions": list(questions)})


def _q(gap_index: int, **kw) -> dict:
    base = {
        "gap_index": gap_index,
        "skip": False,
        "question": kw.get("question", f"Question for gap {gap_index}?"),
        "answer_type": kw.get("answer_type", "boolean"),
        "options": kw.get("options", []),
        "reason": kw.get("reason", "coverage"),
    }
    if "skip" in kw:
        base["skip"] = kw["skip"]
    return base


# -- Severity -> priority mapping ---------------------------------------------


class TestSeverityMapping:
    def _one_gap(self, severity: GapSeverity) -> GapReport:
        return GapReport(
            gaps=[
                Gap(
                    description="d",
                    severity=severity,
                    requirement_id="REQ-001",
                    suggested_question="Q?",
                )
            ]
        )

    def test_blocker_maps_to_blocking(self, requirements, settings, prompts) -> None:
        agent = _agent(settings, prompts, _batch(_q(0)))
        qs = agent.generate_questions(requirements, self._one_gap(GapSeverity.BLOCKER))
        assert qs[0].priority is QuestionPriority.BLOCKING

    def test_major_maps_to_recommended(self, requirements, settings, prompts) -> None:
        agent = _agent(settings, prompts, _batch(_q(0)))
        qs = agent.generate_questions(requirements, self._one_gap(GapSeverity.MAJOR))
        assert qs[0].priority is QuestionPriority.RECOMMENDED

    def test_minor_maps_to_optional(self, requirements, settings, prompts) -> None:
        agent = _agent(settings, prompts, _batch(_q(0)))
        qs = agent.generate_questions(requirements, self._one_gap(GapSeverity.MINOR))
        assert qs[0].priority is QuestionPriority.OPTIONAL


# -- Answer-type selection ----------------------------------------------------


class TestAnswerTypes:
    def _gap(self) -> GapReport:
        return GapReport(
            gaps=[
                Gap(
                    description="d",
                    severity=GapSeverity.MAJOR,
                    requirement_id="REQ-001",
                    suggested_question="Q?",
                )
            ]
        )

    def test_boolean(self, requirements, settings, prompts) -> None:
        agent = _agent(settings, prompts, _batch(_q(0, answer_type="boolean")))
        assert (
            agent.generate_questions(requirements, self._gap())[0].answer_type is AnswerType.BOOLEAN
        )

    def test_single_select(self, requirements, settings, prompts) -> None:
        agent = _agent(
            settings, prompts, _batch(_q(0, answer_type="single_select", options=["A", "B"]))
        )
        q = agent.generate_questions(requirements, self._gap())[0]
        assert q.answer_type is AnswerType.SINGLE_SELECT
        assert q.options == ["A", "B"]

    def test_multi_select(self, requirements, settings, prompts) -> None:
        agent = _agent(
            settings, prompts, _batch(_q(0, answer_type="multi_select", options=["Admin", "User"]))
        )
        assert (
            agent.generate_questions(requirements, self._gap())[0].answer_type
            is AnswerType.MULTI_SELECT
        )

    def test_text_only_when_needed(self, requirements, settings, prompts) -> None:
        agent = _agent(settings, prompts, _batch(_q(0, answer_type="text")))
        assert agent.generate_questions(requirements, self._gap())[0].answer_type is AnswerType.TEXT


# -- Batch, skip, duplicates --------------------------------------------------


class TestBatching:
    def _n_gaps(self, n: int) -> GapReport:
        return GapReport(
            gaps=[
                Gap(
                    description=f"gap {i}",
                    severity=GapSeverity.BLOCKER,
                    requirement_id="REQ-001",
                    suggested_question=f"Q{i}?",
                )
                for i in range(n)
            ]
        )

    def test_max_batch_respected(self, requirements, settings, prompts) -> None:
        # 10 gaps shaped, but batch caps at 7.
        agent = _agent(
            settings,
            prompts,
            _batch(*[_q(i, question=f"Distinct question {i}?") for i in range(10)]),
        )
        qs = agent.generate_questions(requirements, self._n_gaps(10), max_batch=7)
        assert len(qs) == 7

    def test_small_batch_not_padded(self, requirements, settings, prompts) -> None:
        agent = _agent(settings, prompts, _batch(_q(0, question="Only one?")))
        qs = agent.generate_questions(requirements, self._n_gaps(1))
        assert len(qs) == 1

    def test_skip_dropped(self, requirements, settings, prompts) -> None:
        agent = _agent(settings, prompts, _batch(_q(0, question="Keep?"), _q(1, skip=True)))
        qs = agent.generate_questions(requirements, self._n_gaps(2))
        assert len(qs) == 1
        assert qs[0].question == "Keep?"

    def test_duplicate_within_batch_suppressed(self, requirements, settings, prompts) -> None:
        agent = _agent(
            settings,
            prompts,
            _batch(_q(0, question="Same question?"), _q(1, question="Same question?")),
        )
        qs = agent.generate_questions(requirements, self._n_gaps(2))
        assert len(qs) == 1

    def test_duplicate_of_prior_iteration_suppressed(self, requirements, settings, prompts) -> None:
        agent = _agent(settings, prompts, _batch(_q(0, question="Already asked?")))
        prior = ClarificationAgent(
            MockLLMClient([_batch(_q(0, question="Already asked?"))]), prompts, settings
        ).generate_questions(requirements, self._n_gaps(1))
        qs = agent.generate_questions(requirements, self._n_gaps(1), already_asked=prior)
        assert qs == []

    def test_bad_gap_index_ignored(self, requirements, settings, prompts) -> None:
        agent = _agent(settings, prompts, _batch(_q(99, question="Out of range?")))
        assert agent.generate_questions(requirements, self._n_gaps(1)) == []

    def test_empty_gap_report_no_questions(self, requirements, settings, prompts) -> None:
        agent = _agent(settings, prompts, _batch())
        assert agent.generate_questions(requirements, GapReport(gaps=[])) == []


# -- Answer application / augmentation ----------------------------------------


class TestAnswerApplication:
    def _setup(self, settings, prompts, requirements):
        gaps = GapReport(
            gaps=[
                Gap(
                    description="Retry undefined",
                    severity=GapSeverity.BLOCKER,
                    requirement_id="REQ-001",
                    suggested_question="Retry?",
                )
            ]
        )
        agent = _agent(settings, prompts, _batch(_q(0, question="Allow retry?")))
        qs = agent.generate_questions(requirements, gaps)
        return agent, qs

    def test_answer_applied_to_correct_requirement(self, requirements, settings, prompts) -> None:
        agent, qs = self._setup(settings, prompts, requirements)
        ans = [
            ClarificationAnswer(
                question_id=qs[0].question_id, answer_type=AnswerType.BOOLEAN, answer="true"
            )
        ]
        new_reqs, _ = agent.apply_answers(requirements, qs, ans)
        req1 = next(r for r in new_reqs if r.id == "REQ-001")
        req2 = next(r for r in new_reqs if r.id == "REQ-002")
        assert any("Allow retry?" in a for a in req1.assumptions)
        assert req2.assumptions == []  # untouched requirement unchanged

    def test_requirement_id_preserved(self, requirements, settings, prompts) -> None:
        agent, qs = self._setup(settings, prompts, requirements)
        ans = [
            ClarificationAnswer(
                question_id=qs[0].question_id, answer_type=AnswerType.BOOLEAN, answer="true"
            )
        ]
        new_reqs, _ = agent.apply_answers(requirements, qs, ans)
        assert {r.id for r in new_reqs} == {"REQ-001", "REQ-002"}

    def test_requirement_augmented_not_regenerated(self, requirements, settings, prompts) -> None:
        agent, qs = self._setup(settings, prompts, requirements)
        original_desc = requirements[0].description
        ans = [
            ClarificationAnswer(
                question_id=qs[0].question_id, answer_type=AnswerType.BOOLEAN, answer="true"
            )
        ]
        new_reqs, _ = agent.apply_answers(requirements, qs, ans)
        req1 = next(r for r in new_reqs if r.id == "REQ-001")
        # Original description preserved; clarification appended to assumptions.
        assert req1.description == original_desc
        assert len(req1.assumptions) == len(requirements[0].assumptions) + 1

    def test_input_requirements_not_mutated(self, requirements, settings, prompts) -> None:
        agent, qs = self._setup(settings, prompts, requirements)
        ans = [
            ClarificationAnswer(
                question_id=qs[0].question_id, answer_type=AnswerType.BOOLEAN, answer="true"
            )
        ]
        agent.apply_answers(requirements, qs, ans)
        assert requirements[0].assumptions == []  # caller's objects untouched

    def test_question_marked_answered(self, requirements, settings, prompts) -> None:
        agent, qs = self._setup(settings, prompts, requirements)
        ans = [
            ClarificationAnswer(
                question_id=qs[0].question_id, answer_type=AnswerType.BOOLEAN, answer="true"
            )
        ]
        _, updated = agent.apply_answers(requirements, qs, ans)
        assert updated[0].status is QuestionStatus.ANSWERED

    def test_contradictory_answers_rejected(self, requirements, settings, prompts) -> None:
        agent, qs = self._setup(settings, prompts, requirements)
        qid = qs[0].question_id
        ans = [
            ClarificationAnswer(question_id=qid, answer_type=AnswerType.BOOLEAN, answer="true"),
            ClarificationAnswer(question_id=qid, answer_type=AnswerType.BOOLEAN, answer="false"),
        ]
        with pytest.raises(ValueError, match="Contradictory"):
            agent.apply_answers(requirements, qs, ans)


# -- Assumptions --------------------------------------------------------------


class TestAssumptions:
    def _q_list(self, settings, prompts, requirements):
        gaps = GapReport(
            gaps=[
                Gap(
                    description="Timeout undefined",
                    severity=GapSeverity.MAJOR,
                    requirement_id="REQ-001",
                    suggested_question="Timeout?",
                )
            ]
        )
        agent = _agent(settings, prompts, _batch(_q(0, question="Allow timeout retry?")))
        return agent, agent.generate_questions(requirements, gaps)

    def test_skip_creates_assumption(self, requirements, settings, prompts) -> None:
        agent, qs = self._q_list(settings, prompts, requirements)
        updated, assumptions = agent.mark_skipped(qs)
        assert updated[0].status is QuestionStatus.SKIPPED
        assert len(assumptions) == 1
        assert assumptions[0].question_id == qs[0].question_id
        assert assumptions[0].requirement_id == "REQ-001"

    def test_answered_question_not_skipped(self, requirements, settings, prompts) -> None:
        agent, qs = self._q_list(settings, prompts, requirements)
        qs[0].status = QuestionStatus.ANSWERED
        updated, assumptions = agent.mark_skipped(qs)
        assert updated[0].status is QuestionStatus.ANSWERED
        assert assumptions == []


# -- Readiness + iteration ----------------------------------------------------


class TestReadinessAndIteration:
    def _blocking_q(self, settings, prompts, requirements):
        gaps = GapReport(
            gaps=[
                Gap(
                    description="Blocking gap",
                    severity=GapSeverity.BLOCKER,
                    requirement_id="REQ-001",
                    suggested_question="Blocker?",
                )
            ]
        )
        agent = _agent(settings, prompts, _batch(_q(0, question="Blocking decision?")))
        return agent, agent.generate_questions(requirements, gaps)

    def test_blocking_unanswered_not_ready(self, requirements, settings, prompts) -> None:
        agent, qs = self._blocking_q(settings, prompts, requirements)
        state = ClarificationState(run_id="r1", questions=qs)
        refreshed = agent.refresh_readiness(state, critical_gaps=0, requirements_total=2)
        assert refreshed.readiness.ready is False

    def test_blocking_answered_ready(self, requirements, settings, prompts) -> None:
        agent, qs = self._blocking_q(settings, prompts, requirements)
        ans = [
            ClarificationAnswer(
                question_id=qs[0].question_id, answer_type=AnswerType.BOOLEAN, answer="yes"
            )
        ]
        _, updated = agent.apply_answers(requirements, qs, ans)
        state = ClarificationState(run_id="r1", questions=updated)
        refreshed = agent.refresh_readiness(state, critical_gaps=0, requirements_total=2)
        assert refreshed.readiness.ready is True

    def test_remaining_critical_gap_triggers_next_iteration(
        self, requirements, settings, prompts
    ) -> None:
        agent, qs = self._blocking_q(settings, prompts, requirements)
        state = ClarificationState(run_id="r1", questions=qs)
        # Even with the question answered, an open critical gap keeps it not-ready.
        for q in state.questions:
            q.status = QuestionStatus.ANSWERED
        refreshed = agent.refresh_readiness(state, critical_gaps=1, requirements_total=2)
        assert refreshed.readiness.ready is False  # -> caller runs another iteration

    def test_no_blockers_ready(self, requirements, settings, prompts) -> None:
        gaps = GapReport(
            gaps=[
                Gap(
                    description="minor",
                    severity=GapSeverity.MINOR,
                    requirement_id="REQ-001",
                    suggested_question="Minor?",
                )
            ]
        )
        agent = _agent(settings, prompts, _batch(_q(0, question="Optional thing?")))
        qs = agent.generate_questions(requirements, gaps)
        state = ClarificationState(run_id="r1", questions=qs)
        refreshed = agent.refresh_readiness(state, critical_gaps=0, requirements_total=2)
        assert refreshed.readiness.ready is True  # only optional open


# -- Persistence compatibility + malformed responses --------------------------


class TestCompatAndErrors:
    def test_state_persistence_round_trip(self, requirements, settings, prompts, tmp_path) -> None:
        from qaops.clarification import load_clarification_state, write_clarification_state

        gaps = GapReport(
            gaps=[
                Gap(
                    description="g",
                    severity=GapSeverity.BLOCKER,
                    requirement_id="REQ-001",
                    suggested_question="Q?",
                )
            ]
        )
        agent = _agent(settings, prompts, _batch(_q(0, question="Persisted question?")))
        qs = agent.generate_questions(requirements, gaps)
        state = ClarificationState(run_id="r1", questions=qs)
        state = agent.refresh_readiness(state, critical_gaps=0, requirements_total=2)
        write_clarification_state(tmp_path, state)
        assert load_clarification_state(tmp_path) == state

    def test_malformed_llm_response_fails_clearly(self, requirements, settings, prompts) -> None:
        gaps = GapReport(
            gaps=[
                Gap(
                    description="g",
                    severity=GapSeverity.MAJOR,
                    requirement_id="REQ-001",
                    suggested_question="Q?",
                )
            ]
        )
        # Not JSON at all; structured-output layer should raise after repair retries.
        agent = _agent(settings, prompts, "this is not json at all")
        with pytest.raises((StageError, ValueError, Exception)):
            agent.generate_questions(requirements, gaps)

    def test_no_images_passed_to_agent(self, requirements, settings, prompts) -> None:
        # The agent's LLM request must carry no images (text-only).
        gaps = GapReport(
            gaps=[
                Gap(
                    description="g",
                    severity=GapSeverity.MAJOR,
                    requirement_id="REQ-001",
                    suggested_question="Q?",
                )
            ]
        )

        captured = {}

        class _Spy(MockLLMClient):
            def complete(self, request):
                captured["images"] = request.messages[0].images
                return super().complete(request)

        agent = ClarificationAgent(_Spy([_batch(_q(0))]), prompts, settings)
        agent.generate_questions(requirements, gaps)
        assert captured["images"] == []
