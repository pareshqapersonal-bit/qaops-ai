"""Deterministic ID generation.

IDs (REQ-001, BR-001, SC-001, TC-001) are assigned by code after each
generation stage, never by the LLM. This is the architectural invariant
that makes traceability and coverage validation trustworthy: the
validator checks references against IDs it knows were assigned
consistently.
"""

from collections.abc import Iterator


class IdGenerator:
    """Produces sequential zero-padded IDs for a given prefix."""

    def __init__(self, prefix: str, start: int = 1, width: int = 3) -> None:
        if not prefix or not prefix.isalpha():
            msg = f"ID prefix must be alphabetic, got {prefix!r}"
            raise ValueError(msg)
        self._prefix = prefix.upper()
        self._counter = start
        self._width = width

    @property
    def prefix(self) -> str:
        return self._prefix

    def next(self) -> str:
        value = f"{self._prefix}-{self._counter:0{self._width}d}"
        self._counter += 1
        return value

    def take(self, count: int) -> list[str]:
        return [self.next() for _ in range(count)]

    def __iter__(self) -> Iterator[str]:
        while True:
            yield self.next()


def requirement_ids() -> IdGenerator:
    return IdGenerator("REQ")


def business_rule_ids() -> IdGenerator:
    return IdGenerator("BR")


def scenario_ids() -> IdGenerator:
    return IdGenerator("SC")


def test_case_ids() -> IdGenerator:
    return IdGenerator("TC")
