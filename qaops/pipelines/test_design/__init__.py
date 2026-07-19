"""Test Design pipeline stages.

Phase 2 ships requirement analysis: RequirementAnalyzer,
BusinessRuleExtractor, GapAnalyzer. Scenario and test case generation
arrive in Phases 3-4.
"""

from qaops.config import QAOpsSettings
from qaops.core.pipeline import Pipeline
from qaops.llm import LLMClient, PromptLoader
from qaops.pipelines.test_design.analyzer import RequirementAnalyzer
from qaops.pipelines.test_design.gaps import GapAnalyzer
from qaops.pipelines.test_design.rules import BusinessRuleExtractor

__all__ = [
    "BusinessRuleExtractor",
    "GapAnalyzer",
    "RequirementAnalyzer",
    "build_analysis_pipeline",
]


def build_analysis_pipeline(
    client: LLMClient, prompts: PromptLoader, settings: QAOpsSettings
) -> Pipeline:
    """Compose the Phase 2 requirement-analysis pipeline:
    RequirementInput -> analyzer -> rules -> gaps -> RequirementAnalysisResult.
    """
    return Pipeline(
        [
            RequirementAnalyzer(client, prompts, settings),
            BusinessRuleExtractor(client, prompts, settings),
            GapAnalyzer(client, prompts, settings),
        ]
    )
