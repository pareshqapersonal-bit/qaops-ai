"""Canonical mock LLM responses for the Phase 21 pipeline (ADR-036).

Shared by tests that drive the full pipeline through MockLLMClient. The pipeline
sequence is:

    analyzer -> rules -> gaps -> scenarios -> conditions -> cases -> coverage

so a scenario-entry run scripts [conditions, cases] and a requirements-entry run
scripts [rules, gaps, scenarios, conditions, cases]. These builders keep the
condition_id linkage consistent between the condition and test-case responses.
"""

import json


def conditions_response(
    pairs: list[tuple[str, str]] | None = None,
) -> str:
    """A conditions payload. pairs is a list of (scenario_id, requirement_id).

    Defaults to a single SC-001/REQ-001 positive condition.
    """
    pairs = pairs or [("SC-001", "REQ-001")]
    conditions = [
        {
            "scenario_id": sc,
            "requirement_ids": [req] if req else [],
            "business_rule_ids": [],
            "category": "positive",
            "description": f"{sc} primary condition is accepted.",
            "rationale": req or sc,
            "source_basis": "explicit_requirement" if req else "scenario",
            "status": "resolved",
            "parameters": {},
            "gap_reference": "",
        }
        for sc, req in pairs
    ]
    return json.dumps({"conditions": conditions})


def test_cases_response(
    triples: list[tuple[str, str, str]] | None = None,
) -> str:
    """A test-cases payload. triples is (scenario_id, condition_id, requirement_id).

    Defaults to a single SC-001/COND-001/REQ-001 case.
    """
    triples = triples or [("SC-001", "COND-001", "REQ-001")]
    cases = [
        {
            "scenario_id": sc,
            "condition_id": cond,
            "requirement_ids": [req] if req else ["REQ-001"],
            "title": f"{cond} case",
            "expected_result": "the documented outcome occurs",
            "steps": [{"action": "perform the documented action", "expected": "observed"}],
            "priority": "high",
            "test_type": "functional",
        }
        for sc, cond, req in triples
    ]
    return json.dumps({"test_cases": cases})
