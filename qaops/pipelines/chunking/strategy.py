"""ChunkStrategy - decides whether and how a document should be chunked.

Separates the *decision* from the *mechanism* (ADR-021). ChunkPlanner splits
text and applies overlap; it no longer owns any sizing policy. ChunkStrategy
answers three questions - should we chunk, how big, how much overlap - from
document length, provider capability, and a safety margin.

Two strategies:

- adaptive (default): derives chunk size from the model's practical output
  capacity, so users do not tune chunk_size per document or provider.
- fixed: uses the configured chunk_size verbatim, as an advanced override.

Both are pure functions of their inputs, so behavior is deterministic and
testable without an LLM.
"""

from dataclasses import dataclass

from qaops.pipelines.chunking.capabilities import capability_for

# A requirement analysis produces substantially more output than the input
# text it describes - structured JSON with descriptions, actors, validations,
# and verbatim excerpts per requirement. This factor estimates input chars
# that can safely produce output within the model's ceiling. Measured against
# real runs: a 7.6k-char PRD produced ~36k chars of requirement JSON, roughly
# 4.7x. Using 3.0 leaves headroom on top of the safety margin.
OUTPUT_EXPANSION_FACTOR = 3.0

# Overlap as a fraction of chunk size, so context carried between chunks
# scales with the chunks themselves rather than being a fixed constant.
OVERLAP_RATIO = 0.08

# Never plan a chunk smaller than this: tiny chunks fragment requirements
# across boundaries and multiply duplicate extraction.
MIN_CHUNK_SIZE = 2000


@dataclass(frozen=True)
class ChunkDecision:
    """The outcome of a chunking decision."""

    should_chunk: bool
    chunk_size: int
    chunk_overlap: int
    reason: str


class ChunkStrategy:
    """Decides how a document should be chunked."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        safety_margin: float,
        fixed_chunk_size: int | None = None,
        fixed_chunk_overlap: int | None = None,
    ) -> None:
        """
        Args:
            provider: configured provider name, e.g. 'openrouter'.
            model: configured model string for that provider.
            safety_margin: fraction of estimated capacity to actually use
                (0 < margin <= 1). 0.8 means plan to 80% of capacity.
            fixed_chunk_size: when set, the strategy is 'fixed' and this value
                is used verbatim instead of an adaptive calculation.
            fixed_chunk_overlap: overlap to use in fixed mode.
        """
        if not 0 < safety_margin <= 1:
            msg = f"safety_margin must be in (0, 1], got {safety_margin}"
            raise ValueError(msg)
        self._provider = provider
        self._model = model
        self._safety_margin = safety_margin
        self._fixed_chunk_size = fixed_chunk_size
        self._fixed_chunk_overlap = fixed_chunk_overlap

    @property
    def is_adaptive(self) -> bool:
        return self._fixed_chunk_size is None

    def capacity_chars(self) -> int:
        """Input characters this provider/model can safely process in one pass."""
        capability = capability_for(self._provider, self._model)
        usable_output = capability.max_output_chars * self._safety_margin
        return max(MIN_CHUNK_SIZE, int(usable_output / OUTPUT_EXPANSION_FACTOR))

    def decide(self, document_length: int) -> ChunkDecision:
        """Decide whether and how to chunk a document of the given length."""
        if self._fixed_chunk_size is not None:
            size = self._fixed_chunk_size
            overlap = (
                self._fixed_chunk_overlap
                if self._fixed_chunk_overlap is not None
                else int(size * OVERLAP_RATIO)
            )
            should = document_length > size
            return ChunkDecision(
                should_chunk=should,
                chunk_size=size,
                chunk_overlap=min(overlap, max(0, size - 1)),
                reason=(f"fixed strategy: chunk_size={size}, document={document_length} chars"),
            )

        capacity = self.capacity_chars()
        if document_length <= capacity:
            # Bypass entirely - the analyzer runs exactly as it would without
            # any chunking involved.
            return ChunkDecision(
                should_chunk=False,
                chunk_size=max(capacity, document_length),
                chunk_overlap=0,
                reason=(
                    f"adaptive: document {document_length} chars fits within "
                    f"estimated capacity {capacity} for {self._provider}/{self._model}"
                ),
            )

        overlap = int(capacity * OVERLAP_RATIO)
        return ChunkDecision(
            should_chunk=True,
            chunk_size=capacity,
            chunk_overlap=min(overlap, max(0, capacity - 1)),
            reason=(
                f"adaptive: document {document_length} chars exceeds estimated "
                f"capacity {capacity} for {self._provider}/{self._model}"
            ),
        )
