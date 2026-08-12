"""EvidencePackage: the optional visual-evidence carrier for a run (Phase 36).

An internal ingestion/API value object that travels ALONGSIDE the existing
`RequirementInput` (which is NOT modified). It carries the combined text document
(the backbone the pipeline already uses) plus any image evidence with provenance
and ordering. Only the RequirementAnalyzer consumes it; downstream stages work off
derived artifacts and never see it. It is deliberately NOT a domain/pipeline model
and is never placed in `qaops/models/`.

Phase 36 Part 1 defines and plumbs this structure; no provider consumes the images
yet. A text-only run carries an empty image list, so behavior is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qaops.llm.models import ImagePart


@dataclass(frozen=True)
class EvidencePackage:
    """Optional visual evidence accompanying a run's text input.

    `images` preserves upload/extraction order via each ImagePart's `order` and the
    list order itself. Empty `images` means a purely textual run (the default),
    which must behave exactly as before.
    """

    images: list[ImagePart] = field(default_factory=list)

    @property
    def has_images(self) -> bool:
        return bool(self.images)

    def ordered_images(self) -> list[ImagePart]:
        """Images in deterministic order (by `order`, then filename)."""
        return sorted(self.images, key=lambda p: (p.order, p.source_filename))
