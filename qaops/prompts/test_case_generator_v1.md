Write production-quality manual test cases for the designed test scenarios below.

Generate one or more test cases per scenario. A scenario needs multiple test cases when it covers multiple distinct concrete conditions (for example, a boundary scenario may need one case at the limit and one just past it).

Rules:
- Ground every test case in the scenarios, requirements, and business rules provided. Do not invent behavior the documents do not define.
- Each test case must reference exactly one scenario_id and one or more requirement_ids from the provided lists. Use only the given IDs; never invent IDs of any kind.
- Write "steps" as an ordered array in execution order. Do NOT include step numbers; numbering is assigned by the system. Each step has an "action" (what the tester does) and an optional "expected" (what the tester observes after that step).
- Steps must be concrete and executable by a tester who has never seen the application: name the screen, the field, and the exact value entered. Put reusable concrete values in "test_data" as key-value pairs and refer to them in steps.
- "expected_result" states the final verifiable outcome of the whole test case. Mandatory.
- "preconditions" list the state required before step 1 (accounts, data, configuration).
- "objective" states in one sentence what the test case proves.
- "priority" must be exactly one of: critical, high, medium, low. Base it on business impact and likelihood.
- "test_type" must be exactly one of: functional, negative, boundary, validation, permission, state_transition, integration, ui, error_handling.
- "module" and "feature" name the application area under test, derived from the requirements.
- "tags" are short lowercase labels (e.g. "smoke", "regression", "login").
- Titles must be specific and unique. Do NOT generate duplicate test cases: no two test cases for the same scenario may share the same title or test the same concrete condition.

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
      "requirement_ids": ["REQ-001"],
      "module": "Application area",
      "feature": "Feature name",
      "title": "Specific, unique test case title",
      "objective": "What this test case proves.",
      "preconditions": ["..."],
      "test_data": {"field": "value"},
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
