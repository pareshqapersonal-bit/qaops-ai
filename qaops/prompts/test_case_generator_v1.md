Write production-quality manual test cases for the TEST CONDITIONS below.

Each test condition is a single testable proposition already derived from the requirements, business rules, and scenarios. Turn each condition into one or more executable manual test cases. A condition needs more than one case only when distinct data values, boundary points, or states genuinely require separate execution (a boundary condition may need one case at the limit and one just past it). Do NOT pad: do not invent extra cases, and do not merge several conditions into one generic case.

Rules:
- Ground every test case in the conditions, scenarios, requirements, and business rules provided. Do not invent behavior the documents do not define.
- Each test case must reference exactly one condition_id and exactly one scenario_id (the condition's scenario) and one or more requirement_ids, all from the provided lists. Use only the given IDs; never invent IDs of any kind.
- A condition whose status is "unresolved" has NO documented expected behaviour. For such a condition, write the case that exercises the documented steps but set "expected_result" to describe what must be confirmed with the product owner (do not fabricate a pass/fail assertion). Do not skip unresolved conditions.
- Write "steps" as an ordered array in execution order. Do NOT include step numbers; numbering is assigned by the system. Each step has an "action" and an optional "expected".
- Steps must be concrete and executable by a tester who has never seen the application: name the screen, the field, and the exact value. Put reusable concrete values in "test_data" and refer to them in steps. For boundary/data conditions, put the distinguishing value (e.g. the quantity) in "test_data" so variants are distinct.
- "expected_result" states the final verifiable outcome. Mandatory.
- "preconditions" list the state required before step 1.
- "objective" states in one sentence what the case proves.
- "priority" is exactly one of: critical, high, medium, low.
- "test_type" is exactly one of: functional, negative, boundary, validation, permission, state_transition, integration, ui, error_handling.
- "module" and "feature" name the application area under test.
- "tags" are short lowercase labels.
- Titles must be specific and unique. Do NOT generate duplicate test cases: two cases for the same condition testing the same concrete data are duplicates. Boundary variants with different data are NOT duplicates.

Test conditions (with their IDs, scenario, evidence, category, and status):
$conditions_json

Designed scenarios (with their IDs and requirement links):
$scenarios_json

Analyzed requirements (with their IDs):
$requirements_json

Business rules (with their requirement links):
$rules_json

Respond with ONLY this JSON structure, no prose, no markdown fences:

{
  "test_cases": [
    {
      "scenario_id": "SC-001",
      "condition_id": "COND-001",
      "requirement_ids": ["REQ-001"],
      "module": "Application area",
      "feature": "Feature name",
      "title": "Specific, unique test case title",
      "objective": "What this test case proves.",
      "preconditions": ["..."],
      "test_data": {"quantity": "2"},
      "steps": [
        {"action": "What the tester does.", "expected": "What the tester observes."}
      ],
      "expected_result": "The final verifiable outcome.",
      "priority": "critical | high | medium | low",
      "test_type": "one of the listed values",
      "tags": ["..."]
    }
  ]
}
