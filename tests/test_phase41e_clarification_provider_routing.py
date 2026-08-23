"""Phase 41E clarification re-analysis provider-routing tests.

Proves that the iterative-loop re-analysis calls use the normal TEXT provider
chain and do NOT inherit the original image-run/NVIDIA requirement, while the
initial requirement analysis for an image ticket still routes to an image-capable
provider. Asserts the StageRequirements each call builds (captured via a patched
resilient seam) - no live LLM/provider calls.
"""

import json
from contextlib import contextmanager, suppress
from pathlib import Path
from unittest.mock import patch

from qaops.clarification.service import ClarificationService
from qaops.config import QAOpsSettings
from qaops.execution.candidates import build_candidate_models
from qaops.execution.models import ModelRegistry
from qaops.execution.registry import get_provider
from qaops.execution.selector import StageRequirements, select_candidates
from qaops.llm import MockLLMClient
from qaops.models.domain import GapReport


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "run"
    (ws / "input").mkdir(parents=True)
    (ws / "input" / "t.md").write_text("User checks store availability.", encoding="utf-8")
    return ws


def _svc(tmp_path: Path) -> tuple[ClarificationService, Path]:
    ws = _ws(tmp_path)
    return ClarificationService(QAOpsSettings(output_dir=ws / "output")), ws


class TestReanalysisRequirements:
    """Capture the StageRequirements each clarification LLM call passes."""

    def _capture(self, fn, *args) -> StageRequirements:
        captured: dict[str, StageRequirements] = {}

        def fake(*, settings, requirements, run_call, **_kw):
            captured["req"] = requirements
            # Return a benign value of the shape each caller expects.
            raise _Stop

        with (
            patch("qaops.clarification.service.resilient_structured_call", side_effect=fake),
            suppress(_Stop),
        ):
            fn(*args)
        return captured["req"]

    def test_gap_reanalysis_does_not_require_images(self, tmp_path: Path) -> None:
        svc, _ws_ = _svc(tmp_path)
        settings = QAOpsSettings(output_dir=tmp_path / "out")
        req = self._capture(svc._rerun_gap_analysis, [], "src", settings)
        # Text request: no image need AND no image-provider exclusion (so an
        # image-capable provider stays available as a fallback).
        assert req.needs_images is False
        # Phase C: exclude_image_providers removed - the request is purely
        # capability-driven (no image need, no image-provider exclusion).

    def test_question_generation_uses_text_path(self, tmp_path: Path) -> None:
        svc, _ws_ = _svc(tmp_path)
        settings = QAOpsSettings(output_dir=tmp_path / "out")
        req = self._capture(svc._generate_questions, [], GapReport(gaps=[]), settings)
        assert req.needs_images is False
        # Phase C: no exclude_image_providers - capability-driven text request.

    def test_initial_image_analysis_still_requires_images(self, tmp_path: Path) -> None:
        # With image evidence present, the INITIAL requirement analysis must still
        # require an image-capable provider (unchanged NVIDIA routing).
        svc, ws = _svc(tmp_path)
        settings = QAOpsSettings(output_dir=ws / "output")
        captured: dict[str, StageRequirements] = {}

        def fake(*, settings, requirements, run_call, **_kw):
            captured["req"] = requirements
            raise _Stop

        with (
            patch("qaops.clarification.service.load_evidence_package") as load_ev,
            patch("qaops.clarification.service.resilient_structured_call", side_effect=fake),
        ):
            # Simulate an image-bearing run.
            class _Evi:
                images = ["img1"]

            load_ev.return_value = _Evi()
            with suppress(_Stop):
                svc._analyze(ws / "input" / "t.md", settings)
        assert captured["req"].needs_images is True


class TestFallbackSelection:
    """The routing choice is what lets NVIDIA serve as a text fallback."""

    def _models(self, providers):
        return build_candidate_models(
            providers=providers,
            settings=QAOpsSettings(provider="nvidia"),
            registry=ModelRegistry(),
        )

    def test_reanalysis_can_fall_back_to_nvidia_when_only_provider(self) -> None:
        # In an environment where NVIDIA is the only reachable provider, the text
        # re-analysis request (no exclusion) still yields a candidate.
        models = self._models([get_provider("nvidia")])
        req = StageRequirements(needs_structured_output=True)  # the new routing
        cands = select_candidates(models, req, limit=10, configured="nvidia", excluded=set())
        assert len(cands) >= 1
        assert any(c.model.provider == "nvidia" for c in cands)

    def test_nvidia_only_env_still_yields_text_candidate(self) -> None:
        # Capability-driven (Phase C): with only NVIDIA reachable, a text/structured
        # call still yields NVIDIA as a candidate - image capability is never a
        # reason to exclude it. (This is what previously broke: the old
        # exclude_image_providers rule dropped NVIDIA here, leaving zero candidates.)
        models = self._models([get_provider("nvidia")])
        req = StageRequirements(needs_structured_output=True)
        cands = select_candidates(models, req, limit=10, configured="nvidia", excluded=set())
        assert len(cands) >= 1
        assert any(c.model.provider == "nvidia" for c in cands)

    def test_text_run_ranks_text_providers_first(self) -> None:
        # With a full provider set, the text re-analysis request still prefers text
        # providers; NVIDIA is only a fallback, not preferred - so text behaviour is
        # unchanged for normal deployments.
        from qaops.execution.registry import _REGISTRY

        models = self._models(list(_REGISTRY.values()))
        req = StageRequirements(needs_structured_output=True)
        cands = select_candidates(models, req, limit=50, configured="nvidia", excluded=set())
        providers = [c.model.provider for c in cands]
        assert providers[0] != "nvidia"  # a text provider leads
        assert "nvidia" in providers  # but NVIDIA remains eligible as a fallback
        # NVIDIA ranks after the text providers (fallback, not preferred).
        assert providers.index("nvidia") > 0


# --- integration: image-run clarification re-analysis doesn't need NVIDIA ----

_ANALYZER = json.dumps(
    {
        "requirements": [
            {
                "title": "Store availability",
                "description": "User checks store availability.",
                "source_excerpt": "check store availability",
            }
        ]
    }
)
_GAP_BLOCKER = json.dumps(
    {
        "gaps": [
            {
                "description": "Retry undefined",
                "severity": "blocker",
                "requirement_id": "REQ-001",
                "suggested_question": "Retry?",
            }
        ]
    }
)
_GAP_NONE = json.dumps({"gaps": []})
_AGENT = json.dumps(
    {
        "questions": [
            {
                "gap_index": 0,
                "skip": False,
                "question": "Q?",
                "answer_type": "boolean",
                "options": [],
                "reason": "r",
            }
        ]
    }
)


def _patch(client: object):
    return (
        patch("qaops.execution.resilient_call.create_client", return_value=client),
        patch(
            "qaops.execution.resilient_call.fallback_providers",
            return_value=[get_provider("nvidia")],  # NVIDIA-only environment
        ),
    )


@contextmanager
def _clarify(client: object):
    p1, p2 = _patch(client)
    with p1, p2:
        yield


class TestImageRunReanalysisSucceedsOnNvidiaOnly:
    def test_answer_round_succeeds_with_only_nvidia_available(self, tmp_path: Path) -> None:
        # A clarification run whose ONLY reachable provider is NVIDIA must still be
        # able to run the answer-round gap re-analysis + question generation
        # (previously the re-run excluded NVIDIA on image runs and 500'd). We drive
        # start() with a text run (so the initial analyzer uses NVIDIA as the sole
        # text candidate too), then prove the answer round completes on NVIDIA-only.
        from qaops.clarification.enums import AnswerType
        from qaops.clarification.models import ClarificationAnswer

        ws = _ws(tmp_path)
        svc = ClarificationService(QAOpsSettings(output_dir=ws / "output"))

        with _clarify(MockLLMClient([_ANALYZER, _GAP_BLOCKER, _AGENT])):
            state = svc.start("run_1", ws / "input" / "t.md", ws)

        ans = [
            ClarificationAnswer(
                question_id=state.questions[0].question_id,
                answer_type=AnswerType.BOOLEAN,
                answer="true",
            )
        ]
        # Answer round: gap re-analysis runs on the NVIDIA-only fallback and
        # succeeds (no ResilientCallError / 500) because NVIDIA is not excluded.
        with _clarify(MockLLMClient([_GAP_NONE])):
            new = svc.submit_answers(ws, ans)
        assert new.readiness.ready is True


class _Stop(Exception):
    """Sentinel to stop after capturing StageRequirements."""
