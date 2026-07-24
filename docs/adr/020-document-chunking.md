# ADR-020: Chunking is internal to requirement analysis, invisible downstream

**Status:** Accepted · **Date:** 2026-07-23 · **Relates to:** ADR-001, ADR-006, ADR-019

## Context

Real PRDs generate more structured output than a model can return in one
response. ADR-019 added a temporary evaluation mode that capped requirements
to make a demonstration possible; it analyzes only part of a document and was
always meant to be replaced. The real fix is to process a large document in
pieces.

The risk with chunking is that it leaks: if every stage has to know a document
was split, the pipeline gains a cross-cutting concern and six stages need
changing.

## Decision

Chunking is an **internal capability of requirement analysis**, not a pipeline
concern.

1. **`ChunkedRequirementAnalyzer` is a drop-in replacement** for
   `RequirementAnalyzer` at pipeline position 0: same
   `run(RequirementInput) -> RequirementAnalysisResult` signature, and
   deliberately **the same stage name**, `requirement_analyzer`. The name is
   observable in error messages and pipeline introspection, so renaming it
   would leak the implementation detail — and did, in fact, break five
   existing tests until it was reverted. That breakage was useful evidence:
   the design principle is testable.
2. **The existing analyzer is reused unmodified**, once per chunk. No prompt
   changes, no prompt variants, no second analyzer implementation.
3. **`ChunkPlanner` contains no QA-specific logic.** It splits text on
   semantic boundaries (headings, then paragraphs, then lines, then a hard
   cut) and knows nothing about requirements. It is deterministic and testable
   without an LLM.
4. **`merge_requirements` produces one canonical result.** Overlapping chunks
   yield duplicates, and each chunk's analyzer run assigns its own `REQ-001..`
   sequence, so the merge deduplicates and assigns one fresh, gap-free ID
   sequence. Deduplication keys on the normalized title — the model's own
   summary of the requirement — and keeps the richer of two duplicates so
   detail found in one chunk is not lost to a sparser copy.
5. **Small documents are unaffected.** A document within `chunk_size` takes a
   single-chunk path that delegates straight to the wrapped analyzer, so
   existing behavior and call counts are unchanged.

### Merging at the domain layer, not the wire layer

The obvious design merges `RequirementExtraction` (the LLM wire schema). That
is wrong here: the analyzer returns `RequirementAnalysisResult`, a domain model
carrying `source_name`, the full `source_text`, and ID-assigned `Requirement`
objects. Merging at the domain layer is what keeps downstream stages unaware —
they receive exactly the model they always did, with the full document as
`source_text` rather than a chunk.

## Consequences

- Large PRDs no longer require evaluation mode. ADR-019 remains available but
  is no longer the answer for document size, and should be removed once this
  path is proven on real documents.
- Business rules, gaps, scenarios, test cases, coverage, and exporters are
  **completely unchanged** — verified by the existing suite passing untouched.
- Cost: N chunks means N analyzer calls, so a large document costs more and
  takes longer. Duplicate requirements across chunk boundaries are removed by
  title matching, which is conservative: two genuinely distinct requirements
  that happen to share a title would be merged. Semantic deduplication is
  deliberately out of scope.
- A chunk yielding no requirements (a table of contents, an appendix) is
  tolerated and skipped; only a document where *every* chunk yields nothing is
  a failure.
- Downstream stages still generate per requirement, so a very large document
  can still exceed output limits in later stages. Chunking those stages is a
  separate decision, deliberately not taken here.
