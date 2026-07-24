"""PipelineBuilder - the minimal valid pipeline for a given entry point (ADR-022).

Builds from the existing stages only. No stage is modified, subclassed, or
duplicated: each entry point simply starts further down the same sequence, so
a stage cannot tell which route was taken - it receives the domain model it
always has.

    DOCUMENT     analyzer -> rules -> gaps -> scenarios -> cases -> coverage
    REQUIREMENTS             rules -> gaps -> scenarios -> cases -> coverage
    SCENARIOS                                             cases -> coverage
"""

from qaops.config import QAOpsSettings
from qaops.core.pipeline import Pipeline
from qaops.entrypoints.entry_point import EntryPoint
from qaops.llm import LLMClient, PromptLoader
from qaops.pipelines.chunking import ChunkedRequirementAnalyzer
from qaops.pipelines.test_design.coverage import CoverageValidator
from qaops.pipelines.test_design.gaps import GapAnalyzer
from qaops.pipelines.test_design.rules import BusinessRuleExtractor
from qaops.pipelines.test_design.scenarios import ScenarioGenerator
from qaops.pipelines.test_design.test_cases import TestCaseGenerator


def build_pipeline_for(
    entry_point: EntryPoint,
    client: LLMClient,
    prompts: PromptLoader,
    settings: QAOpsSettings,
) -> Pipeline:
    """Compose the minimal pipeline that takes `entry_point` input to a
    fully validated TestDesignResult."""
    analyzer = ChunkedRequirementAnalyzer(client, prompts, settings)
    rules = BusinessRuleExtractor(client, prompts, settings)
    gaps = GapAnalyzer(client, prompts, settings)
    scenarios = ScenarioGenerator(client, prompts, settings)
    cases = TestCaseGenerator(client, prompts, settings)
    coverage = CoverageValidator()

    if entry_point is EntryPoint.DOCUMENT:
        return Pipeline([analyzer, rules, gaps, scenarios, cases, coverage])
    if entry_point is EntryPoint.REQUIREMENTS:
        return Pipeline([rules, gaps, scenarios, cases, coverage])
    # EntryPoint is exhaustive, so this is the SCENARIOS case.
    return Pipeline([cases, coverage])


def stage_names_for(entry_point: EntryPoint) -> list[str]:
    """The stage names a given entry point will run, for diagnostics and CLI output."""
    full = [
        "requirement_analyzer",
        "business_rule_extractor",
        "gap_analyzer",
        "scenario_generator",
        "test_case_generator",
        "coverage_validator",
    ]
    if entry_point is EntryPoint.DOCUMENT:
        return full
    if entry_point is EntryPoint.REQUIREMENTS:
        return full[1:]
    return full[4:]
