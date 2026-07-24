"""ChunkPlanner - deterministic text splitting (ADR-020).

Splits text into overlapping chunks that fit within a model's practical
input/output budget. Contains NO QA-specific logic: it knows about
characters, paragraphs, and headings, not requirements or test cases. That
separation is deliberate - the planner is reusable for any future stage
that needs to process long text, and it can be tested without any LLM.

Boundary preference, in order: markdown headings, then paragraph breaks,
then line breaks, then a hard character cut. Preferring a semantic boundary
keeps a requirement from being sliced mid-sentence, which would produce two
partial requirements instead of one whole one.

Deterministic: identical input and settings always yield identical chunks.
"""

import re
from dataclasses import dataclass

# A markdown heading at the start of a line, e.g. "## Section".
_HEADING = re.compile(r"^#{1,6} ", re.MULTILINE)


@dataclass(frozen=True)
class Chunk:
    """One slice of a document, with its position in the original text."""

    index: int
    total: int
    text: str
    start: int
    end: int


class ChunkPlanner:
    """Splits text into overlapping chunks on semantic boundaries."""

    def __init__(self, chunk_size: int, chunk_overlap: int) -> None:
        if chunk_size <= 0:
            msg = "chunk_size must be positive"
            raise ValueError(msg)
        if chunk_overlap < 0:
            msg = "chunk_overlap must not be negative"
            raise ValueError(msg)
        if chunk_overlap >= chunk_size:
            msg = "chunk_overlap must be smaller than chunk_size"
            raise ValueError(msg)
        self._size = chunk_size
        self._overlap = chunk_overlap

    def plan(self, text: str) -> list[Chunk]:
        """Split text into chunks. Text shorter than chunk_size yields one chunk."""
        if not text:
            return []
        if len(text) <= self._size:
            return [Chunk(index=1, total=1, text=text, start=0, end=len(text))]

        spans: list[tuple[int, int]] = []
        start = 0
        while start < len(text):
            end = min(start + self._size, len(text))
            if end < len(text):
                end = self._find_boundary(text, start, end)
            spans.append((start, end))
            if end >= len(text):
                break
            # Step forward, keeping `overlap` characters of context.
            next_start = end - self._overlap
            # Guarantee forward progress even if a boundary landed early.
            start = next_start if next_start > start else end

        total = len(spans)
        return [
            Chunk(index=i, total=total, text=text[s:e], start=s, end=e)
            for i, (s, e) in enumerate(spans, start=1)
        ]

    def _find_boundary(self, text: str, start: int, end: int) -> int:
        """Move `end` back to the nearest semantic boundary, if one is close.

        Only looks within the last quarter of the chunk, so a boundary far
        from the target does not produce a uselessly small chunk.
        """
        window_start = max(start + 1, end - self._size // 4)
        window = text[window_start:end]

        # 1. Markdown heading - the strongest boundary.
        headings = list(_HEADING.finditer(window))
        if headings:
            return window_start + headings[-1].start()

        # 2. Paragraph break.
        para = window.rfind("\n\n")
        if para != -1:
            return window_start + para + 2

        # 3. Line break.
        line = window.rfind("\n")
        if line != -1:
            return window_start + line + 1

        # 4. Hard cut at the requested size.
        return end
