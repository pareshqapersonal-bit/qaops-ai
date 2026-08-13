"""Image evidence sidecar: persist/reload ImageParts alongside a run (Phase 36B Part 2).

Serialization lives OUTSIDE the 36A models (ImagePart/EvidencePackage are unchanged).
The sidecar is a single deterministic JSON file in the run workspace, separate from
the pipeline input file, so the single-file input contract is preserved and no
binary/base64 data is written into the combined Markdown.

Format (list of objects, upload order preserved):
    [{"media_type", "data", "source_filename", "order", "page", "image_index"}, ...]

Only the fields ImagePart already exposes are stored; reload reconstructs ImageParts
exactly (byte-for-byte base64), then an EvidencePackage.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from qaops.ingestion.evidence import EvidencePackage
from qaops.llm import ImagePart

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

# The sidecar lives beside input/ and output/ in the run workspace.
_EVIDENCE_DIRNAME = "evidence"
_IMAGES_FILENAME = "images.json"


class EvidenceSidecarError(RuntimeError):
    """The image sidecar exists but could not be read or reconstructed.

    Raised on corrupt/unreadable sidecar content so an image-bearing run fails
    clearly rather than silently downgrading to text-only.
    """


def sidecar_path(workspace: Path) -> Path:
    """Path to the image sidecar for a run workspace."""
    return workspace / _EVIDENCE_DIRNAME / _IMAGES_FILENAME


def write_image_sidecar(workspace: Path, images: Sequence[ImagePart]) -> Path:
    """Serialize ImageParts to the run's sidecar (deterministic, upload order).

    Writes nothing and returns the path when `images` is empty is NOT the contract:
    callers only invoke this when there are images. Each part is dumped via its own
    model serialization so only known ImagePart fields are stored.
    """
    payload = [part.model_dump(mode="json") for part in images]
    target = sidecar_path(workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return target


def load_evidence_package(workspace: Path) -> EvidencePackage | None:
    """Reconstruct an EvidencePackage from the sidecar, or None if there is none.

    Returns None when no sidecar file exists (a text/document-only run), so the
    caller leaves evidence as None and the existing execution path is unchanged.
    Raises EvidenceSidecarError if the sidecar exists but is corrupt/unreadable or
    an entry fails ImagePart validation - never silently dropping visual evidence.
    """
    path = sidecar_path(workspace)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvidenceSidecarError(f"Image evidence sidecar is unreadable: {exc}") from exc
    if not isinstance(raw, list):
        raise EvidenceSidecarError("Image evidence sidecar is malformed (expected a list).")
    try:
        images = [ImagePart.model_validate(entry) for entry in raw]
    except Exception as exc:  # noqa: BLE001 - reconstruction failure must fail the run
        raise EvidenceSidecarError(
            f"Image evidence sidecar could not be reconstructed: {exc}"
        ) from exc
    return EvidencePackage(images=images)
