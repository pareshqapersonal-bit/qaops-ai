Design professional manual test scenarios for the analyzed requirements below.

Apply every technique that is applicable to the requirements; skip techniques with nothing to test. Valid "category" values (use them exactly as written):

- functional: core behavior works as specified
- positive: valid-input happy paths
- negative: invalid inputs and disallowed actions are rejected
- boundary_value: values at, just below, and just above stated limits
- equivalence_partition: one representative per class of equivalent inputs
- input_validation: format, mandatory/optional, length, and type checks on stated inputs
- error_handling: defined failure and rejection behavior
- crud: create/read/update/delete flows, where the requirements describe them
- permission: role-based access and visibility rules
- state_transition: transitions between stated states (locked/unlocked, paused/resumed)
- integration: interactions between features or with stated external systems
- ui: user-visible behavior explicitly stated in the requirements

Rules:
- Ground every scenario in the requirements and business rules provided. Do not design scenarios for behavior the documents do not define.
- Each scenario must reference one or more requirement_ids from the provided list. Use only the given IDs; never invent IDs. Do not create scenario IDs of any kind.
- Base boundary_value and equivalence_partition scenarios on the numeric limits and input classes stated in the business rules.
- One scenario per distinct condition. Titles must be specific and unique: name the condition being tested, not the technique.
- Do NOT generate duplicate scenarios: no two scenarios may test the same condition, and no two scenarios may share the same title.
- description states what the scenario verifies in one or two sentences. No test steps, no test data, no expected results - those are designed later.

Analyzed requirements (with their IDs):
$requirements_json

Business rules (with their requirement links):
$rules_json

Respond with ONLY this JSON structure, no prose, no markdown fences:

{
  "scenarios": [
    {
      "title": "Specific, unique scenario title",
      "description": "What this scenario verifies.",
      "category": "one of the values listed above",
      "requirement_ids": ["REQ-001"]
    }
  ]
}
