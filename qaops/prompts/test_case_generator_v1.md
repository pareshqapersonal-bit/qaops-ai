Write production-quality manual test cases by filling the EXPANSION PLAN below.

Each test condition has already been decomposed, deterministically, into one or more expansion SLOTS. Each slot is one required test-case variant (for example the below/at/above points of a boundary, or one representative per equivalence partition). Your job is to author exactly ONE concrete, executable test case for EACH slot — no more, no fewer.

Hard rules about the plan:
- Produce exactly one test case per slot. Do NOT invent extra slots, do NOT skip slots, do NOT merge slots.
- Echo the slot's "slot_id" in the test case's "slot_id" field so it maps back to the plan.
- Use the slot's "parameter_delta" as the concrete distinguishing data for that case: put those key/values into "test_data" and use them in the steps. This is what makes each variant genuinely different (e.g. quantity 1 vs 2 vs 3).
- The slot's "technique" and "variant_label" tell you what the case must exercise; set "test_type" consistently (boundary slot -> boundary, negative -> negative, state_transition -> state_transition, etc.).

Grounding and evidence:
- Ground every test case in the conditions, scenarios, requirements, and business rules provided. Do not invent behavior the documents do not define.
- Each test case references exactly one condition_id and one scenario_id (the condition's scenario) and one or more requirement_ids, all from the provided lists. Use only the given IDs; never invent IDs.
- A slot whose technique is "provisional" belongs to an UNRESOLVED condition with NO documented expected behaviour. Author the case that exercises the documented steps, but set "expected_result" to state what must be confirmed with the product owner. Do NOT fabricate a pass/fail assertion, and do NOT skip it.

Writing the case:
- "steps" is an ordered array in execution order. Do NOT include step numbers; numbering is assigned by the system. Each step has an "action" and an optional "expected".
- Steps must be concrete and executable by a tester who has never seen the application: name the screen, the field, and the exact value. Reference the values you put in "test_data".
- "expected_result" states the final verifiable outcome. Mandatory (except provisional slots, per above).
- "preconditions" list required state before step 1. "objective" states in one sentence what the case proves.
- "priority" is exactly one of: critical, high, medium, low.
- "test_type" is exactly one of: functional, negative, boundary, validation, permission, state_transition, integration, ui, error_handling.
- "module" and "feature" name the application area. "tags" are short lowercase labels; include the technique as a tag.
- Titles must be specific and unique, and should reflect the variant (e.g. "... at quantity 1 (below threshold)").

Do NOT generate duplicate cases: two cases with the same condition and the same concrete data are duplicates. Variants that come from different slots (different parameter_delta) are NOT duplicates and must each appear.

Expansion plan (one case per slot):
$expansion_plan_json

Test conditions (with their IDs, scenario, evidence, category, status):
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
      "slot_id": "COND-001-S1",
      "requirement_ids": ["REQ-001"],
      "module": "Application area",
      "feature": "Feature name",
      "title": "Specific, unique title reflecting the variant",
      "objective": "What this test case proves.",
      "preconditions": ["..."],
      "test_data": {"quantity": "1"},
      "steps": [
        {"action": "What the tester does, using the test_data values.", "expected": "What the tester observes."}
      ],
      "expected_result": "The final verifiable outcome.",
      "priority": "high",
      "test_type": "boundary",
      "tags": ["boundary"]
    }
  ]
}
