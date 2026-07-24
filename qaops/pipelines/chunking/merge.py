"""RequirementMerge - combines per-chunk analyses into one canonical result.

Chunks overlap, so the same requirement can be extracted more than once,
and each chunk's analyzer run assigns its own REQ-001.. sequence. This
module removes duplicates and assigns one fresh, gap-free set of canonical
IDs, producing a RequirementAnalysisResult indistinguishable from an
unchunked run (ADR-020).

Deduplication is deterministic and conservative: two requirements are the
same when their normalized titles match. Titles are the model's own summary
of the requirement, so identical titles across overlapping chunks reliably
indicate the same requirement, while genuinely distinct requirements rarely
share one. Descriptions and list fields vary too much between chunks to key
on, and semantic (embedding) matching is deliberately out of scope.

When duplicates are found, the richer record wins: the one with more
populated metadata is kept, so detail extracted in one chunk is not lost
because a sparser copy appeared first.
"""

from qaops.core.ids import requirement_ids
from qaops.models import Requirement, RequirementAnalysisResult

# Fields whose contents count toward "richness" when choosing between duplicates.
_METADATA_FIELDS = (
    "actors",
    "inputs",
    "outputs",
    "validations",
    "dependencies",
    "constraints",
    "assumptions",
)


def _normalized_title(title: str) -> str:
    return " ".join(title.casefold().split())


def _richness(requirement: Requirement) -> int:
    """How much populated metadata a requirement carries."""
    score = sum(len(getattr(requirement, field)) for field in _METADATA_FIELDS)
    score += len(requirement.description)
    score += len(requirement.source_excerpt)
    return score


def merge_requirements(
    results: list[RequirementAnalysisResult],
    *,
    source_name: str,
    source_text: str,
) -> RequirementAnalysisResult:
    """Merge per-chunk analyses into one canonical RequirementAnalysisResult.

    Order is preserved by first appearance, so output is deterministic for a
    given chunk sequence. IDs are reassigned from REQ-001 with no gaps.

    Args:
        results: per-chunk analyses, in chunk order.
        source_name: the original document name.
        source_text: the full original text, not a chunk.
    """
    best_by_title: dict[str, Requirement] = {}
    order: list[str] = []

    for result in results:
        for requirement in result.requirements:
            key = _normalized_title(requirement.title)
            existing = best_by_title.get(key)
            if existing is None:
                best_by_title[key] = requirement
                order.append(key)
            elif _richness(requirement) > _richness(existing):
                # Keep the richer duplicate, but hold its original position.
                best_by_title[key] = requirement

    ids = requirement_ids()
    merged = [best_by_title[key].model_copy(update={"id": ids.next()}) for key in order]
    return RequirementAnalysisResult(
        source_name=source_name,
        source_text=source_text,
        requirements=merged,
    )
