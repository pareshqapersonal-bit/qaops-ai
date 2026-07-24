"""Multi-entry pipeline composition (ADR-022).

Real QA teams rarely start from a PRD: they often already have requirements
or scenarios. An entry point names where a run joins the existing pipeline;
parsers turn external files into canonical domain models; PipelineBuilder
composes the minimal stage sequence. No pipeline stage, prompt, model, or
exporter changes - stages never learn which route was taken.
"""

from qaops.entrypoints.builder import build_pipeline_for, stage_names_for
from qaops.entrypoints.entry_point import EntryPoint
from qaops.entrypoints.parsers import parse_requirements, parse_scenarios

__all__ = [
    "EntryPoint",
    "build_pipeline_for",
    "parse_requirements",
    "parse_scenarios",
    "stage_names_for",
]
