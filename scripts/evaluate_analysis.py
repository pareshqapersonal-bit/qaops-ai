"""Manual evaluation script - NOT production code.

Runs the real Anthropic model over a golden example and pretty-prints
every pipeline output for human quality review (ADR-008: output quality
is judged by a person, not by CI). Delete or ignore freely; nothing
imports this.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python scripts/evaluate_analysis.py [examples/checkout.md]
"""

import sys
from pathlib import Path

from qaops.config import QAOpsSettings
from qaops.llm import AnthropicClient, PromptLoader
from qaops.models import RequirementInput, ScenarioDesignResult
from qaops.pipelines.test_design import build_scenario_pipeline

RULE = "=" * 78


def header(title: str) -> None:
    print(f"\n{RULE}\n{title}\n{RULE}")


def bullet_list(label: str, items: list[str]) -> None:
    if items:
        print(f"    {label}: {'; '.join(items)}")


def main() -> None:
    source = Path(sys.argv[1] if len(sys.argv) > 1 else "examples/login.md")
    text = source.read_text(encoding="utf-8")

    settings = QAOpsSettings()
    client = AnthropicClient(model=settings.model)
    prompts = PromptLoader(version=settings.prompt_version)
    pipeline = build_scenario_pipeline(client, prompts, settings)

    print(f"Model: {settings.model} | prompts: {settings.prompt_version} | input: {source}")
    result = pipeline.run(RequirementInput(text=text, source_name=source.name))
    assert isinstance(result, ScenarioDesignResult)
    analysis = result.analysis

    header(f"REQUIREMENT ANALYSIS ({len(analysis.requirements)})")
    for req in analysis.requirements:
        print(f"\n[{req.id}] {req.title}")
        print(f"    {req.description}")
        if req.source_excerpt:
            print(f'    Grounded in: "{req.source_excerpt}"')
        bullet_list("Actors", req.actors)
        bullet_list("Inputs", req.inputs)
        bullet_list("Validations", req.validations)
        bullet_list("Constraints", req.constraints)

    header(f"BUSINESS RULES ({len(analysis.business_rules)})")
    for rule in analysis.business_rules:
        print(f"[{rule.id} -> {rule.requirement_id}] {rule.rule}")

    gaps = analysis.gap_report.gaps
    header(f"GAP REPORT ({len(gaps)}, blockers: {analysis.gap_report.has_blockers})")
    for gap in gaps:
        ref = gap.requirement_id or "document-wide"
        print(f"\n[{gap.severity.value.upper()}] ({ref}) {gap.description}")
        if gap.suggested_question:
            print(f"    Ask the BA/PO: {gap.suggested_question}")

    header(f"GENERATED SCENARIOS ({len(result.scenarios)})")
    by_category: dict[str, int] = {}
    for sc in result.scenarios:
        by_category[sc.category.value] = by_category.get(sc.category.value, 0) + 1
        refs = ", ".join(sc.requirement_ids)
        print(f"\n[{sc.id}] ({sc.category.value}) {sc.title}  <- {refs}")
        if sc.description:
            print(f"    {sc.description}")

    header("SCENARIOS BY TECHNIQUE")
    for category, count in sorted(by_category.items()):
        print(f"    {category:24s} {count}")


if __name__ == "__main__":
    main()
