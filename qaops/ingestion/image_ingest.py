"""Image attachment ingestion for tickets (Phase 36B Part 1).

Turns an uploaded PNG/JPEG into a 36A ImagePart, deterministically and without OCR,
Pillow, or any full decode. Validation is by magic-byte signature only. This module
is isolated ingestion logic: it constructs ImageParts but does NOT persist them or
thread them into the execution path (that is a later, separately-approved part).

Limits (centralized here, not duplicated in the endpoint):
- at most MAX_IMAGE_COUNT image attachments per ticket,
- at most MAX_IMAGE_BYTES per image,
- at most MAX_TOTAL_IMAGE_BYTES across all images.

Document attachments (pdf/docx/md/txt) are handled elsewhere and do not count here.
"""

from __future__ import annotations

import base64

from qaops.llm.models import ImagePart

# Supported image types, mapped to the exact media_type ImagePart already accepts.
# PNG and JPEG only; WEBP is intentionally excluded (ImagePart has no image/webp).
_IMAGE_SUFFIX_TO_MEDIA_TYPE: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

# Magic-byte signatures. PNG: 8-byte signature. JPEG: starts FF D8 FF.
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"

# Centralized limits (Phase 36B decision: 5 images / 10 MB each / 25 MB total).
MAX_IMAGE_COUNT = 5
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 25 * 1024 * 1024

IMAGE_SUFFIXES = frozenset(_IMAGE_SUFFIX_TO_MEDIA_TYPE)


class ImageValidationError(ValueError):
    """An uploaded image failed validation. Carries a client-safe message.

    The endpoint maps this to a 400 naming the offending file; raising a plain,
    typed error here keeps this module free of any web/HTTP dependency.
    """


def is_image_suffix(suffix: str) -> bool:
    """Whether a lowercase file suffix denotes a supported image type."""
    return suffix.lower() in _IMAGE_SUFFIX_TO_MEDIA_TYPE


def _signature_matches(suffix: str, data: bytes) -> bool:
    media_type = _IMAGE_SUFFIX_TO_MEDIA_TYPE[suffix]
    if media_type == "image/png":
        return data.startswith(_PNG_SIGNATURE)
    return data.startswith(_JPEG_SIGNATURE)  # image/jpeg


def build_image_part(*, filename: str, suffix: str, data: bytes, order: int) -> ImagePart:
    """Validate one uploaded image and construct its 36A ImagePart.

    Validation (magic-byte only, no decode):
    - non-empty,
    - supported extension,
    - magic bytes match the claimed PNG/JPEG type,
    - within the per-image size cap.

    Raises ImageValidationError (client-safe message) on any failure. The original
    bytes are base64-encoded verbatim so the image is preserved exactly; provenance
    is the filename and the upload-order index.
    """
    normalized = suffix.lower()
    if normalized not in _IMAGE_SUFFIX_TO_MEDIA_TYPE:
        supported = ", ".join(sorted(_IMAGE_SUFFIX_TO_MEDIA_TYPE))
        raise ImageValidationError(
            f"Unsupported image type {suffix or '(none)'} for '{filename}'. "
            f"Supported image types: {supported}."
        )
    if not data:
        raise ImageValidationError(f"Image '{filename}' is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageValidationError(
            f"Image '{filename}' is {len(data)} bytes, exceeding the "
            f"{MAX_IMAGE_BYTES}-byte per-image limit."
        )
    if not _signature_matches(normalized, data):
        raise ImageValidationError(
            f"Image '{filename}' does not have a valid "
            f"{_IMAGE_SUFFIX_TO_MEDIA_TYPE[normalized]} signature."
        )
    return ImagePart(
        media_type=_IMAGE_SUFFIX_TO_MEDIA_TYPE[normalized],  # type: ignore[arg-type]
        data=base64.b64encode(data).decode("ascii"),
        source_filename=filename,
        order=order,
    )


def check_image_budget(*, count: int, total_bytes: int) -> None:
    """Enforce the aggregate image-count and total-payload limits.

    Raises ImageValidationError if the number of images or the cumulative byte total
    exceeds the configured maxima. Called by the endpoint as images accumulate.
    """
    if count > MAX_IMAGE_COUNT:
        raise ImageValidationError(
            f"Too many image attachments: {count}. At most {MAX_IMAGE_COUNT} are allowed."
        )
    if total_bytes > MAX_TOTAL_IMAGE_BYTES:
        raise ImageValidationError(
            f"Total image payload is {total_bytes} bytes, exceeding the "
            f"{MAX_TOTAL_IMAGE_BYTES}-byte limit."
        )
