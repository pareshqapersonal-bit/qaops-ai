"""Deterministic Jira-style ticket -> Markdown normalization (Phase 32, ADR-047).

The TicketNormalizer is a pure, deterministic transcription layer. It turns a
validated ticket request into a Markdown document that enters the EXISTING
DOCUMENT pipeline (via the text/markdown loader) - it is NOT a second pipeline and
NOT a downstream stage.

Hard rules (a QA design pack is only trustworthy if the input is faithful):
  * transcription only - never invent requirements, business rules, expected
    values, or acceptance criteria, and never semantically rewrite the ticket;
  * acceptance criteria are preserved verbatim and in order (only a "N. " index
    is prepended);
  * optional metadata (ticket_id / priority / labels) appears only when supplied;
  * missing information is simply absent, so the existing GapAnalyzer /
    TestConditionAnalyzer surface it as genuine gaps rather than the normalizer
    papering over it;
  * output is a numbered list, never a Markdown table and never a scenario marker,
    so classify_input keeps the document on the DOCUMENT route.

No LLM is used here and none may be added: the normalizer must stay deterministic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qaops.api.schemas import TicketRequest


def _clean(value: str | None) -> str:
    """Return a stripped value, or "" when absent/whitespace-only."""
    return value.strip() if value and value.strip() else ""


def ticket_to_markdown(ticket: TicketRequest) -> str:
    """Transcribe a ticket into Markdown for the existing DOCUMENT loader.

    Deterministic: the same ticket always yields the same Markdown. The document
    loader (TextLoader + normalize_text) will additionally normalize whitespace on
    load, exactly as it does for any uploaded Markdown file.
    """
    lines: list[str] = [f"# {ticket.title.strip()}", ""]

    # Optional provenance/metadata header lines - only when supplied.
    ticket_id = _clean(ticket.ticket_id)
    priority = _clean(ticket.priority)
    labels = [label.strip() for label in ticket.labels if label and label.strip()]
    header: list[str] = []
    if ticket_id:
        header.append(f"Ticket: {ticket_id}")
    if priority:
        header.append(f"Priority: {priority}")
    if labels:
        header.append(f"Labels: {', '.join(labels)}")
    if header:
        lines.extend(header)
        lines.append("")

    # Description - preserved verbatim (only outer whitespace trimmed).
    lines.append("## Description")
    lines.append("")
    lines.append(ticket.description.strip())

    # Acceptance criteria - verbatim, in order, numbered. Phase 35: when there are
    # none, the section is omitted entirely (no empty heading) rather than left as
    # a bare heading. A ticket with criteria is unchanged.
    criteria = [c for c in ticket.acceptance_criteria if c is not None]
    if criteria:
        lines.append("")
        lines.append("## Acceptance Criteria")
        lines.append("")
        for index, criterion in enumerate(criteria, start=1):
            lines.append(f"{index}. {criterion}")

    return "\n".join(lines).strip() + "\n"


def append_reference_material(markdown: str, *, filename: str, text: str) -> str:
    """Append an attachment as a delimited Design / Reference Material section (Phase 35).

    Deterministic, transcription-only: the extracted attachment text is embedded
    verbatim as document EVIDENCE - the existing pipeline decides what, if anything,
    constitutes a requirement. This never parses or interprets the attachment. The
    section is plain prose/headings only (no table or scenario markers), so the
    combined document still classifies as DOCUMENT.
    """
    base = markdown.rstrip("\n")
    section = f"## Design / Reference Material\nSource: {filename}\n\n{text.strip()}"
    return f"{base}\n\n{section}\n"
