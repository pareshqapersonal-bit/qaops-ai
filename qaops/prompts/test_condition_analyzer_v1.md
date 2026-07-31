Decompose each test scenario below into materially distinct, evidence-bound TEST CONDITIONS.

A test condition is a single testable proposition — one specific situation whose expected behaviour can be checked. A scenario may yield one condition or many. Do NOT aim for a fixed number; derive exactly the conditions the evidence justifies, and no more.

Apply a test-design technique ONLY when the supplied requirements and business rules make it relevant. Do not apply a technique that has nothing to test. Relevant techniques and the "category" value to use:

- positive: documented valid behaviour succeeds
- negative: documented invalid input or disallowed action is rejected
- boundary: values at, just below, and just above a stated numeric limit
- equivalence: one representative per stated input class
- validation: format / mandatory / length / type checks on stated inputs
- eligibility: eligible vs ineligible per a stated eligibility rule
- state_transition: a transition between stated states
- alternate_flow: a documented alternative path
- error_handling: a documented failure/rejection path
- business_rule: a documented rule outcome or rule combination
- data_variation: a distinct documented data case
- role_variation: a documented actor/role difference
- combination: a valid combination of documented conditions

Evidence rules (mandatory):
- Ground every condition in the scenarios, requirements, and business rules provided. Never invent behaviour the documents do not define.
- Reference only the given IDs; never invent IDs of any kind. A condition may reference its scenario_id (exactly one), requirement_ids, and business_rule_ids from the provided lists. Requirement IDs must be ones linked to that scenario.
- Set "source_basis" to the evidence type:
  - explicit_requirement: stated directly by a requirement (must cite a requirement_id)
  - explicit_rule: stated directly by a business rule (must cite a business_rule_id)
  - scenario: implied by the scenario itself
  - derived_boundary: a boundary derived from a stated numeric limit (MUST cite the rule/requirement carrying the limit)
  - derived_equivalence: a class derived from a stated input domain (MUST cite the rule/requirement)
  - documented_combination: a valid combination of stated rules (MUST cite them)
  - documented_state_transition: a transition between stated states (MUST cite the rule/requirement)
- "description" states the single condition in one sentence (e.g. "Cart quantity exactly at the eligibility threshold of 2").
- "rationale" briefly says why the evidence justifies it (e.g. "BR-007 sets the BOGO threshold at quantity >= 2").
- "parameters" captures the dimension values that make this condition distinct (e.g. {"quantity": "2", "eligibility": "eligible"}). Use these for boundary/equivalence/combination conditions so variants remain distinct.

Ambiguity (mandatory):
- If a condition is meaningful but the documents do NOT specify the expected behaviour, set "status" to "unresolved" and put the open question in "gap_reference" (e.g. "Behaviour when an eligible item is removed after the offer is applied is not specified."). Do NOT invent an expected result. Otherwise set "status" to "resolved".

Do NOT produce duplicate conditions: two conditions that express the same proposition with the same parameters are duplicates. Boundary variants with different parameter values (quantity 1 vs 2 vs 3) are NOT duplicates and should each appear when justified.

Designed scenarios (with their IDs and requirement links):
$scenarios_json

Analyzed requirements (with their IDs):
$requirements_json

Business rules (with their IDs and requirement links):
$rules_json

Respond with ONLY this JSON structure, no prose, no markdown fences:

{
  "conditions": [
    {
      "scenario_id": "SC-001",
      "requirement_ids": ["REQ-001"],
      "business_rule_ids": ["BR-001"],
      "category": "boundary",
      "description": "Cart quantity exactly at the eligibility threshold.",
      "rationale": "Why the evidence justifies this condition.",
      "source_basis": "derived_boundary",
      "status": "resolved",
      "parameters": {"quantity": "2"},
      "gap_reference": ""
    }
  ]
}
