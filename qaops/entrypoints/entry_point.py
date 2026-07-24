"""Entry points into the test-design pipeline (ADR-022).

An entry point names where a run joins the existing pipeline. The stages
themselves are unchanged and unaware of the route taken: they receive the
same domain models they always have.

    DOCUMENT   -> analyzer -> rules -> gaps -> scenarios -> cases -> coverage
    REQUIREMENTS ->            rules -> gaps -> scenarios -> cases -> coverage
    SCENARIOS  ->                                           cases -> coverage
"""

from enum import StrEnum


class EntryPoint(StrEnum):
    """Where an input joins the pipeline."""

    DOCUMENT = "document"
    REQUIREMENTS = "requirements"
    SCENARIOS = "scenarios"
