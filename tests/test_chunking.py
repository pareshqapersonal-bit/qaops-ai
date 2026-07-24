"""Phase 11 tests: large-document chunking (ADR-020).

Covers the ChunkPlanner (determinism, overlap, boundaries), the merge engine
(deduplication, ID reassignment, metadata preservation), and pipeline
integration proving downstream stages receive the same model types they
always have. No LLM calls beyond the scripted MockLLMClient."""

import json

import pytest

from qaops.config import QAOpsSettings
from qaops.core.errors import StageError
from qaops.llm import LLMResponse, MockLLMClient, PromptLoader
from qaops.models import (
    Requirement,
    RequirementAnalysisResult,
    RequirementInput,
    TestDesignResult,
)
from qaops.pipelines.chunking import ChunkedRequirementAnalyzer, ChunkPlanner, merge_requirements
from qaops.pipelines.test_design import build_full_pipeline


def extraction(*titles: str) -> str:
    return json.dumps(
        {"requirements": [{"title": t, "description": f"description of {t}"} for t in titles]}
    )


def long_document(sections: int = 6, body_repeat: int = 8) -> str:
    return "\n\n".join(
        f"## Section {i}\n" + ("Requirement body text with detail. " * body_repeat)
        for i in range(sections)
    )


class TestChunkPlannerBasics:
    def test_short_text_yields_single_chunk(self) -> None:
        planner = ChunkPlanner(chunk_size=1000, chunk_overlap=100)
        chunks = planner.plan("a short document")
        assert len(chunks) == 1
        assert chunks[0].index == 1
        assert chunks[0].total == 1
        assert chunks[0].text == "a short document"

    def test_empty_text_yields_no_chunks(self) -> None:
        assert ChunkPlanner(chunk_size=1000, chunk_overlap=100).plan("") == []

    def test_long_text_is_split(self) -> None:
        planner = ChunkPlanner(chunk_size=500, chunk_overlap=100)
        chunks = planner.plan(long_document())
        assert len(chunks) > 1
        assert all(c.total == len(chunks) for c in chunks)
        assert [c.index for c in chunks] == list(range(1, len(chunks) + 1))

    def test_deterministic(self) -> None:
        planner = ChunkPlanner(chunk_size=500, chunk_overlap=100)
        text = long_document()
        first = [(c.start, c.end, c.text) for c in planner.plan(text)]
        second = [(c.start, c.end, c.text) for c in planner.plan(text)]
        assert first == second

    def test_chunks_cover_the_whole_document(self) -> None:
        planner = ChunkPlanner(chunk_size=500, chunk_overlap=100)
        text = long_document()
        chunks = planner.plan(text)
        assert chunks[0].start == 0
        assert chunks[-1].end == len(text)
        # Consecutive chunks are contiguous or overlapping, never gapped.
        for previous, current in zip(chunks, chunks[1:], strict=False):
            assert current.start <= previous.end

    def test_overlap_repeats_context(self) -> None:
        planner = ChunkPlanner(chunk_size=500, chunk_overlap=100)
        chunks = planner.plan(long_document())
        # With overlap, each chunk after the first starts before the previous ended.
        assert any(c.start < prev.end for prev, c in zip(chunks, chunks[1:], strict=False))

    def test_zero_overlap_is_allowed(self) -> None:
        planner = ChunkPlanner(chunk_size=500, chunk_overlap=0)
        chunks = planner.plan(long_document())
        for previous, current in zip(chunks, chunks[1:], strict=False):
            assert current.start == previous.end


class TestChunkPlannerValidation:
    def test_rejects_non_positive_size(self) -> None:
        with pytest.raises(ValueError, match="chunk_size must be positive"):
            ChunkPlanner(chunk_size=0, chunk_overlap=0)

    def test_rejects_negative_overlap(self) -> None:
        with pytest.raises(ValueError, match="must not be negative"):
            ChunkPlanner(chunk_size=100, chunk_overlap=-1)

    def test_rejects_overlap_at_or_above_size(self) -> None:
        with pytest.raises(ValueError, match="smaller than chunk_size"):
            ChunkPlanner(chunk_size=100, chunk_overlap=100)


class TestChunkPlannerBoundaries:
    def test_prefers_heading_boundary(self) -> None:
        planner = ChunkPlanner(chunk_size=120, chunk_overlap=10)
        text = "x" * 100 + "\n\n## New Section\n" + "y" * 200
        chunks = planner.plan(text)
        # The first chunk should end at the heading rather than mid-heading.
        assert not chunks[0].text.rstrip().endswith("## New")

    def test_prefers_paragraph_boundary(self) -> None:
        planner = ChunkPlanner(chunk_size=120, chunk_overlap=10)
        text = "a" * 100 + "\n\n" + "b" * 200
        chunks = planner.plan(text)
        assert len(chunks) > 1

    def test_hard_cut_when_no_boundary_exists(self) -> None:
        planner = ChunkPlanner(chunk_size=100, chunk_overlap=10)
        chunks = planner.plan("z" * 350)  # no newlines at all
        assert len(chunks) > 1
        assert all(len(c.text) <= 100 for c in chunks)


class TestMergeEngine:
    def _result(self, *requirements: Requirement) -> RequirementAnalysisResult:
        return RequirementAnalysisResult(
            source_name="chunk", source_text="text", requirements=list(requirements)
        )

    def test_removes_duplicate_titles(self) -> None:
        a = self._result(
            Requirement(id="REQ-001", title="Login", description="d"),
            Requirement(id="REQ-002", title="Logout", description="d"),
        )
        b = self._result(
            Requirement(id="REQ-001", title="Login", description="d"),
            Requirement(id="REQ-002", title="Search", description="d"),
        )
        merged = merge_requirements([a, b], source_name="doc.pdf", source_text="full")
        assert [r.title for r in merged.requirements] == ["Login", "Logout", "Search"]

    def test_title_matching_is_case_and_whitespace_insensitive(self) -> None:
        a = self._result(Requirement(id="REQ-001", title="User Login", description="d"))
        b = self._result(Requirement(id="REQ-001", title="user   login", description="d"))
        merged = merge_requirements([a, b], source_name="doc.pdf", source_text="full")
        assert len(merged.requirements) == 1

    def test_assigns_fresh_sequential_ids(self) -> None:
        a = self._result(
            Requirement(id="REQ-007", title="A", description="d"),
            Requirement(id="REQ-009", title="B", description="d"),
        )
        b = self._result(Requirement(id="REQ-001", title="C", description="d"))
        merged = merge_requirements([a, b], source_name="doc.pdf", source_text="full")
        assert [r.id for r in merged.requirements] == ["REQ-001", "REQ-002", "REQ-003"]

    def test_keeps_the_richer_duplicate(self) -> None:
        sparse = self._result(Requirement(id="REQ-001", title="Login", description="short"))
        rich = self._result(
            Requirement(
                id="REQ-001",
                title="Login",
                description="a much longer and more complete description",
                actors=["User", "Admin"],
                validations=["email required", "password required"],
            )
        )
        merged = merge_requirements([sparse, rich], source_name="doc.pdf", source_text="full")
        assert len(merged.requirements) == 1
        assert merged.requirements[0].actors == ["User", "Admin"]
        assert len(merged.requirements[0].validations) == 2

    def test_preserves_first_appearance_order(self) -> None:
        a = self._result(Requirement(id="REQ-001", title="Zebra", description="d"))
        b = self._result(Requirement(id="REQ-001", title="Apple", description="d"))
        merged = merge_requirements([a, b], source_name="doc.pdf", source_text="full")
        assert [r.title for r in merged.requirements] == ["Zebra", "Apple"]

    def test_uses_full_source_text_not_chunk_text(self) -> None:
        a = self._result(Requirement(id="REQ-001", title="A", description="d"))
        merged = merge_requirements([a], source_name="doc.pdf", source_text="the complete document")
        assert merged.source_text == "the complete document"
        assert merged.source_name == "doc.pdf"


class TestChunkedAnalyzer:
    def test_small_document_delegates_with_one_call(self) -> None:
        settings = QAOpsSettings(chunking_strategy="fixed", chunk_size=5000, chunk_overlap=100)
        client = MockLLMClient([extraction("Login")])
        result = ChunkedRequirementAnalyzer(client, PromptLoader(), settings).run(
            RequirementInput(text="a short document", source_name="s.md")
        )
        assert client.call_count == 1
        assert [r.id for r in result.requirements] == ["REQ-001"]

    def test_large_document_calls_analyzer_per_chunk(self) -> None:
        settings = QAOpsSettings(chunking_strategy="fixed", chunk_size=500, chunk_overlap=100)
        text = long_document()
        expected_chunks = len(ChunkPlanner(chunk_size=500, chunk_overlap=100).plan(text))
        client = MockLLMClient([extraction("Login")] * expected_chunks)
        ChunkedRequirementAnalyzer(client, PromptLoader(), settings).run(
            RequirementInput(text=text, source_name="prd.pdf")
        )
        assert client.call_count == expected_chunks

    def test_returns_the_same_model_type_as_the_plain_analyzer(self) -> None:
        settings = QAOpsSettings(chunking_strategy="fixed", chunk_size=500, chunk_overlap=100)
        text = long_document()
        chunks = len(ChunkPlanner(chunk_size=500, chunk_overlap=100).plan(text))
        client = MockLLMClient([extraction("Login", "Logout")] * chunks)
        result = ChunkedRequirementAnalyzer(client, PromptLoader(), settings).run(
            RequirementInput(text=text, source_name="prd.pdf")
        )
        assert isinstance(result, RequirementAnalysisResult)
        assert result.source_name == "prd.pdf"
        assert result.source_text == text  # full document, not a chunk

    def test_deduplicates_across_chunks(self) -> None:
        settings = QAOpsSettings(chunking_strategy="fixed", chunk_size=500, chunk_overlap=100)
        text = long_document()
        chunks = len(ChunkPlanner(chunk_size=500, chunk_overlap=100).plan(text))
        client = MockLLMClient([extraction("Login", "Logout")] * chunks)
        result = ChunkedRequirementAnalyzer(client, PromptLoader(), settings).run(
            RequirementInput(text=text, source_name="prd.pdf")
        )
        assert [r.title for r in result.requirements] == ["Login", "Logout"]
        assert [r.id for r in result.requirements] == ["REQ-001", "REQ-002"]

    def test_tolerates_a_chunk_with_no_requirements(self) -> None:
        # A table-of-contents chunk yielding nothing must not fail the run.
        settings = QAOpsSettings(chunking_strategy="fixed", chunk_size=500, chunk_overlap=100)
        text = long_document()
        chunks = len(ChunkPlanner(chunk_size=500, chunk_overlap=100).plan(text))
        scripts: list[str | LLMResponse | Exception] = [
            json.dumps({"requirements": []})
        ] * 3  # empty -> StageError per chunk
        scripts += [extraction("Login")] * (chunks * 3)
        client = MockLLMClient(scripts)
        result = ChunkedRequirementAnalyzer(client, PromptLoader(), settings).run(
            RequirementInput(text=text, source_name="prd.pdf")
        )
        assert [r.title for r in result.requirements] == ["Login"]

    def test_fails_when_every_chunk_yields_nothing(self) -> None:
        settings = QAOpsSettings(chunking_strategy="fixed", chunk_size=500, chunk_overlap=100)
        text = long_document()
        chunks = len(ChunkPlanner(chunk_size=500, chunk_overlap=100).plan(text))
        client = MockLLMClient([json.dumps({"requirements": []})] * (chunks * 4))
        with pytest.raises(StageError, match="No requirements extracted from any"):
            ChunkedRequirementAnalyzer(client, PromptLoader(), settings).run(
                RequirementInput(text=text, source_name="prd.pdf")
            )


class TestSettings:
    def test_chunk_defaults(self) -> None:
        settings = QAOpsSettings()
        assert settings.chunk_size == 6000
        assert settings.chunk_overlap == 500

    def test_overlap_must_be_smaller_than_size(self) -> None:
        with pytest.raises(ValueError, match="must be smaller than"):
            QAOpsSettings(chunk_size=1000, chunk_overlap=1000)


class TestPipelineIntegration:
    def test_downstream_stages_are_unaware_of_chunking(self, tmp_path: object) -> None:
        settings = QAOpsSettings(chunking_strategy="fixed", chunk_size=500, chunk_overlap=100)
        text = long_document(sections=4)
        chunks = len(ChunkPlanner(chunk_size=500, chunk_overlap=100).plan(text))
        scripts: list[str | LLMResponse | Exception] = [extraction("Login", "Logout")] * chunks
        scripts += [
            json.dumps(
                {"rules": [{"requirement_id": "REQ-001", "rule": "a rule", "source_excerpt": ""}]}
            ),
            json.dumps({"gaps": []}),
            json.dumps(
                {
                    "scenarios": [
                        {
                            "title": "valid login",
                            "description": "d",
                            "category": "positive",
                            "requirement_ids": ["REQ-001"],
                        }
                    ]
                }
            ),
            json.dumps(
                {
                    "test_cases": [
                        {
                            "scenario_id": "SC-001",
                            "requirement_ids": ["REQ-001"],
                            "title": "login works",
                            "expected_result": "dashboard",
                            "steps": [{"action": "submit", "expected": "ok"}],
                            "priority": "high",
                            "test_type": "functional",
                        }
                    ]
                }
            ),
        ]
        client = MockLLMClient(scripts)
        result = build_full_pipeline(client, PromptLoader(), settings).run(
            RequirementInput(text=text, source_name="prd.pdf")
        )
        assert isinstance(result, TestDesignResult)
        assert [r.title for r in result.requirements] == ["Login", "Logout"]
        assert len(result.business_rules) == 1
        assert len(result.scenarios) == 1
        assert len(result.test_cases) == 1
        assert result.coverage.metrics.total_requirements == 2
