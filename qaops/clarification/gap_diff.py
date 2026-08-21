"""Pure gap-diff + duplicate-prevention layer (Phase 41E-2).

Compares the latest GapReport against clarification history and classifies each
relevant gap as NEW / PERSISTING / RESOLVED / ACCEPTED. This is the pure
comparison/signature layer ONLY: it has no LLM, no I/O, no state mutation, and is
not yet wired into the clarification service (that is a later 41E phase).

Gap signature (approved): ``requirement_id`` + normalized gap ``description``.
Severity and suggested_question are deliberately NOT part of the signature - a gap
whose severity changes but whose (requirement, description) is unchanged is the
same gap and must not be re-asked.

Determinism / purity guarantees:
- Same inputs -> same output, every call (safe to call repeatedly).
- Inputs (GapReport, signature collections) are never mutated.
- Normalization is conservative and deterministic: whitespace trim + internal
  whitespace collapse + case-fold. It never rewrites, summarizes, paraphrases, or
  semantically matches - two materially different descriptions stay distinct.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from qaops.models.domain import Gap, GapReport

# Collapse any run of Unicode whitespace to a single space (deterministic).
_WHITESPACE = re.compile(r"\s+")

# Field separator inside a signature. The requirement-id component is encoded so a
# missing id (None) can never collide with a real id that happens to contain the
# separator or the literal text "null"/"None" (approved requirement).
_SEP = "\x1f"  # ASCII unit separator - not expected in requirement ids/descriptions
_REQ_PRESENT = "id"
_REQ_ABSENT = "none"


class GapClassification(StrEnum):
    """How a gap relates to the clarification history."""

    NEW = "new"
    PERSISTING = "persisting"
    RESOLVED = "resolved"
    ACCEPTED = "accepted"


def normalize_gap_description(description: str) -> str:
    """Conservatively normalize a gap description for signature comparison.

    Trims, collapses internal whitespace to single spaces, and case-folds. It does
    NOT alter meaning - no rewriting, summarizing, paraphrasing, or semantic
    matching. Purely lexical so two materially different descriptions never merge.
    """
    collapsed = _WHITESPACE.sub(" ", description).strip()
    return collapsed.casefold()


def gap_signature(requirement_id: str | None, description: str) -> str:
    """Deterministic signature for a gap: requirement id + normalized description.

    ``requirement_id`` None is preserved as distinct from any real id: the id
    component is tagged present/absent, so a literal "null"/"None" requirement id
    can never collide with an actual missing id, and two gaps with the same
    normalized description but different requirement ids never collide.
    """
    req_part = _REQ_ABSENT if requirement_id is None else f"{_REQ_PRESENT}{_SEP}{requirement_id}"
    return f"{req_part}{_SEP}{normalize_gap_description(description)}"


def gap_signature_for(gap: Gap) -> str:
    """Signature for a domain Gap (requirement_id + normalized description)."""
    return gap_signature(gap.requirement_id, gap.description)


@dataclass(frozen=True)
class ClassifiedGap:
    """One current gap paired with its signature and classification."""

    gap: Gap
    signature: str
    classification: GapClassification


@dataclass(frozen=True)
class GapDiff:
    """Result of diffing a GapReport against clarification history.

    ``current`` preserves the order of the input GapReport's gaps (each tagged NEW,
    PERSISTING, or ACCEPTED). ``resolved_signatures`` are previously-asked
    signatures no longer present in the current report, in a deterministic
    (sorted) order since they have no natural report order. Convenience tuples
    (``new``, ``persisting``, ``accepted``) are report-ordered subsets of
    ``current``.
    """

    current: tuple[ClassifiedGap, ...] = ()
    resolved_signatures: tuple[str, ...] = ()

    @property
    def new(self) -> tuple[ClassifiedGap, ...]:
        return tuple(c for c in self.current if c.classification is GapClassification.NEW)

    @property
    def persisting(self) -> tuple[ClassifiedGap, ...]:
        return tuple(c for c in self.current if c.classification is GapClassification.PERSISTING)

    @property
    def accepted(self) -> tuple[ClassifiedGap, ...]:
        return tuple(c for c in self.current if c.classification is GapClassification.ACCEPTED)


def diff_gaps(
    report: GapReport,
    *,
    asked_signatures: Iterable[str] = (),
    accepted_signatures: Iterable[str] = (),
) -> GapDiff:
    """Classify the gaps in ``report`` against clarification history.

    Args:
        report: the latest GapReport (not mutated).
        asked_signatures: signatures of gaps already turned into questions
            (e.g. ClarificationState.asked_gap_signatures). Not mutated.
        accepted_signatures: signatures of gaps explicitly accepted as assumptions.
            Kept separate from asked so an accepted gap can never be reclassified
            NEW. Not mutated.

    Classification per current gap (accepted takes precedence over asked):
        - signature in accepted_signatures -> ACCEPTED
        - else signature in asked_signatures -> PERSISTING
        - else                              -> NEW

    Plus RESOLVED: any asked signature not present among the current gaps'
    signatures (and not accepted) - a previously asked gap that has disappeared.

    Order: ``current`` follows the report order; ``resolved_signatures`` is sorted
    for determinism. Duplicate current gaps (same signature) are each classified
    independently and all retained in report order, so the caller can decide how to
    de-duplicate when generating questions.
    """
    asked = frozenset(asked_signatures)
    accepted = frozenset(accepted_signatures)

    current: list[ClassifiedGap] = []
    present_signatures: set[str] = set()
    for gap in report.gaps:
        sig = gap_signature_for(gap)
        present_signatures.add(sig)
        if sig in accepted:
            classification = GapClassification.ACCEPTED
        elif sig in asked:
            classification = GapClassification.PERSISTING
        else:
            classification = GapClassification.NEW
        current.append(ClassifiedGap(gap=gap, signature=sig, classification=classification))

    # Previously asked, not accepted, and no longer present -> resolved.
    resolved = sorted(sig for sig in asked if sig not in present_signatures and sig not in accepted)

    return GapDiff(current=tuple(current), resolved_signatures=tuple(resolved))
