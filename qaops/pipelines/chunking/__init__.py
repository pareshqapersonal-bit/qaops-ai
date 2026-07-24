"""Large-document chunking (ADR-020).

Chunking is an internal capability of requirement analysis, invisible to
every downstream stage: ChunkedRequirementAnalyzer has the same signature
as RequirementAnalyzer and returns the same RequirementAnalysisResult, so
business rules, gaps, scenarios, test cases, coverage, and exporters are
unchanged.

ChunkPlanner contains no QA-specific logic - it splits text on semantic
boundaries and nothing more.
"""

from qaops.pipelines.chunking.analyzer import ChunkedRequirementAnalyzer
from qaops.pipelines.chunking.capabilities import ModelCapability, capability_for
from qaops.pipelines.chunking.merge import merge_requirements
from qaops.pipelines.chunking.planner import Chunk, ChunkPlanner
from qaops.pipelines.chunking.strategy import ChunkDecision, ChunkStrategy

__all__ = [
    "Chunk",
    "ChunkDecision",
    "ChunkPlanner",
    "ChunkStrategy",
    "ChunkedRequirementAnalyzer",
    "ModelCapability",
    "capability_for",
    "merge_requirements",
]
