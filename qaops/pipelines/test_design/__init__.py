"""Test Design pipeline stages.

Phases 2-4: RequirementAnalyzer, BusinessRuleExtractor, GapAnalyzer,
ScenarioGenerator, TestCaseGenerator. Coverage validation arrives in
Phase 5.
"""

from qaops.config import QAOpsSettings
from qaops.core.pipeline import Pipeline
from qaops.llm import LLMClient, PromptLoader
from qaops.pipelines.test_design.analyzer import RequirementAnalyzer
from qaops.pipelines.test_design.gaps import GapAnalyzer
from qaops.pipelines.test_design.rules import BusinessRuleExtractor
from qaops.pipelines.test_design.scenarios import ScenarioGenerator
from qaops.pipelines.test_design.test_cases import TestCaseGenerator

__all__ = [
    "BusinessRuleExtractor",
    "GapAnalyzer",
    "RequirementAnalyzer",
    "ScenarioGenerator",
    "TestCaseGenerator",
    "build_analysis_pipeline",
    "build_scenario_pipeline",
    "build_test_design_pipeline",
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


def build_scenario_pipeline(
    client: LLMClient, prompts: PromptLoader, settings: QAOpsSettings
) -> Pipeline:
    """Compose the Phase 3 pipeline: RequirementInput -> analyzer -> rules
    -> gaps -> scenario generator -> ScenarioDesignResult.
    """
    return Pipeline(
        [
            RequirementAnalyzer(client, prompts, settings),
            BusinessRuleExtractor(client, prompts, settings),
            GapAnalyzer(client, prompts, settings),
            ScenarioGenerator(client, prompts, settings),
        ]
    )


def build_test_design_pipeline(
    client: LLMClient, prompts: PromptLoader, settings: QAOpsSettings
) -> Pipeline:
    """Compose the Phase 4 pipeline: RequirementInput -> analyzer -> rules
    -> gaps -> scenarios -> test cases -> TestDesignResult.
    """
    return Pipeline(
        [
            RequirementAnalyzer(client, prompts, settings),
            BusinessRuleExtractor(client, prompts, settings),
            GapAnalyzer(client, prompts, settings),
            ScenarioGenerator(client, prompts, settings),
            TestCaseGenerator(client, prompts, settings),
        ]
    )
