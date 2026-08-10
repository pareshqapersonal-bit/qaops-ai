"""Phase 32 tests: TicketRequest validation + deterministic TicketNormalizer (ADR-047).

Covers the transcription guarantees: verbatim description and acceptance criteria,
optional-metadata handling, empty criteria allowed, determinism, and no fabricated
content. No LLM, no pipeline - the normalizer is a pure formatting layer.
"""

import pytest
from pydantic import ValidationError

from qaops.api.schemas import TicketRequest
from qaops.ingestion.ticket_normalizer import ticket_to_markdown

_OTP_CRITERIA = [
    "OTP should be sent to the registered mobile number.",
    "Valid OTP should log the user in.",
    "Invalid OTP should show an appropriate error.",
    "Expired OTP should not allow login.",
]


def _ticket(**overrides: object) -> TicketRequest:
    base: dict[str, object] = {
        "title": "Add OTP login",
        "description": "Users should be able to log in using their mobile number and OTP.",
        "acceptance_criteria": list(_OTP_CRITERIA),
    }
    base.update(overrides)
    return TicketRequest(**base)  # type: ignore[arg-type]


class TestTicketRequestValidation:
    def test_title_required(self) -> None:
        with pytest.raises(ValidationError):
            TicketRequest(title="", description="d", acceptance_criteria=[])

    def test_description_required(self) -> None:
        with pytest.raises(ValidationError):
            TicketRequest(title="t", description="", acceptance_criteria=[])

    def test_empty_acceptance_criteria_allowed(self) -> None:
        ticket = TicketRequest(title="t", description="d", acceptance_criteria=[])
        assert ticket.acceptance_criteria == []

    def test_optional_fields_default(self) -> None:
        ticket = TicketRequest(title="t", description="d")
        assert ticket.ticket_id is None
        assert ticket.priority is None
        assert ticket.labels == []


class TestDeterministicNormalization:
    def test_same_ticket_same_markdown(self) -> None:
        ticket = _ticket(ticket_id="OTP-123", priority="High", labels=["auth", "login"])
        assert ticket_to_markdown(ticket) == ticket_to_markdown(ticket)

    def test_full_ticket_structure(self) -> None:
        md = ticket_to_markdown(
            _ticket(ticket_id="OTP-123", priority="High", labels=["auth", "login"])
        )
        assert md.startswith("# Add OTP login")
        assert "Ticket: OTP-123" in md
        assert "Priority: High" in md
        assert "Labels: auth, login" in md
        assert "## Description" in md
        assert "## Acceptance Criteria" in md


class TestVerbatimPreservation:
    def test_every_criterion_verbatim_and_ordered(self) -> None:
        md = ticket_to_markdown(_ticket())
        lines = md.splitlines()
        for index, criterion in enumerate(_OTP_CRITERIA, start=1):
            assert f"{index}. {criterion}" in lines

    def test_description_preserved(self) -> None:
        desc = "Users should be able to log in using their mobile number and OTP."
        md = ticket_to_markdown(_ticket(description=desc))
        assert desc in md


class TestOptionalMetadata:
    def test_absent_metadata_omits_header_lines(self) -> None:
        md = ticket_to_markdown(_ticket())  # no id/priority/labels
        assert "Ticket:" not in md
        assert "Priority:" not in md
        assert "Labels:" not in md

    def test_partial_metadata_only_supplied_lines(self) -> None:
        md = ticket_to_markdown(_ticket(ticket_id="OTP-9"))
        assert "Ticket: OTP-9" in md
        assert "Priority:" not in md
        assert "Labels:" not in md

    def test_whitespace_only_metadata_treated_absent(self) -> None:
        md = ticket_to_markdown(_ticket(ticket_id="   ", priority="  "))
        assert "Ticket:" not in md
        assert "Priority:" not in md

    def test_empty_criteria_yields_heading_only(self) -> None:
        md = ticket_to_markdown(_ticket(acceptance_criteria=[]))
        assert "## Acceptance Criteria" in md
        # No numbered items follow.
        assert "1. " not in md


class TestNoFabrication:
    def test_output_contains_only_supplied_content(self) -> None:
        # A minimal ticket must not gain requirements, rules, or expected values.
        md = ticket_to_markdown(
            TicketRequest(title="Add OTP login", description="Users log in with OTP.")
        )
        lowered = md.lower()
        # None of these invented concepts should appear from a bare ticket.
        for invented in ("expiry", "30 seconds", "requirement", "business rule", "lockout"):
            assert invented not in lowered

    def test_no_markdown_table_emitted(self) -> None:
        # A table would risk misrouting to the SCENARIOS entry point.
        md = ticket_to_markdown(_ticket())
        assert "|" not in md
