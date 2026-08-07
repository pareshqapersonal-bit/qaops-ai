Decompose each test scenario below into materially distinct, evidence-bound TEST CONDITIONS.

A test condition is a single testable proposition — one specific situation whose expected behaviour can be checked. A scenario is often broader than a single condition: analyse each scenario for every documented test dimension it touches and produce a separate condition for each materially distinct one. A scenario may yield one condition or many. Do NOT aim for a fixed number, and do NOT assume one condition per scenario — derive exactly the conditions the evidence justifies, and no more.

Apply a test-design technique ONLY when the supplied requirements and business rules make it relevant. Do not apply a technique that has nothing to test, and do not generate one of every category. Relevant techniques and the "category" value to use:

- positive: documented valid behaviour succeeds
- negative: documented invalid input or disallowed action is rejected
- boundary: values at, just below, and just above a stated numeric limit
- equivalence: one representative per stated input class
- validation: format / mandatory / length / type / exact-copy checks on stated inputs
- eligibility: eligible vs ineligible per a stated eligibility rule
- state_transition: a transition between stated states
- alternate_flow: a documented alternative path
- error_handling: a documented failure/rejection path
- business_rule: a documented rule outcome or rule combination
- data_variation: a distinct documented data case
- role_variation: a documented actor/role difference
- combination: a valid combination of documented conditions (e.g. a decision table)

How to decompose a scenario (do this for each scenario):
1. List the documented dimensions the scenario touches: eligible vs ineligible, presence vs absence of a required configuration/mapping, each documented offer or data class, each documented state, each stated exact-copy string, each documented boundary.
2. Produce one condition per materially distinct, independently testable combination that has a KNOWN expected outcome from the evidence.
3. Combine two dimensions only when the documents show they interact and change the expected outcome (a decision table). Do NOT produce combinations for undocumented states just to fill a grid.

Worked example (illustrative — do not copy its content):
A scenario "CTA visibility" backed by a rule "show the CTA for eligible items only when an eligible PLP mapping is available" documents two interacting dimensions (eligibility × mapping). That yields distinct conditions such as:
  - eligible + mapping present -> CTA shown  (category: business_rule / combination, source_basis: documented_combination)
  - eligible + mapping absent  -> CTA hidden (category: negative / combination, source_basis: documented_combination)
If another rule says ineligible items never show the CTA, add:
  - ineligible -> CTA hidden (category: negative)
That single scenario legitimately produced three conditions — because the evidence documented each outcome. It would be WRONG to also invent "ineligible + mapping present vs absent" as separate conditions if the documents say ineligibility alone hides the CTA (the mapping is irrelevant then).

Evidence rules (mandatory):
- Ground every condition in the scenarios, requirements, and business rules provided. Never invent behaviour the documents do not define.
- Reference only the given IDs; never invent IDs of any kind. A condition references its scenario_id (exactly one), requirement_ids, and business_rule_ids from the provided lists. Requirement IDs must be ones linked to that scenario.
- Set "source_basis" to the evidence type:
  - explicit_requirement: stated directly by a requirement (must cite a requirement_id)
  - explicit_rule: stated directly by a business rule (must cite a business_rule_id)
  - scenario: implied by the scenario itself
  - derived_boundary: a boundary derived from a stated numeric limit (MUST cite the rule/requirement carrying the limit)
  - derived_equivalence: a class derived from a stated input domain (MUST cite the rule/requirement)
  - documented_combination: a valid combination of stated rules (MUST cite them)
  - documented_state_transition: a transition between stated states (MUST cite the rule/requirement)
- "description" states the single condition in one sentence.
- "rationale" briefly says why the evidence justifies it.
- "parameters" captures the dimension values that make this condition distinct (e.g. {"eligibility": "eligible", "mapping": "present"}). Use these for boundary/equivalence/combination conditions so variants remain distinct and are not deduplicated.

Category/behaviour consistency (mandatory):
- A "negative" condition must describe a situation where the criteria are NOT met or an action is disallowed. Never write a negative condition whose description says the criteria ARE met. Keep the category consistent with the described situation.

Resolved vs unresolved (mandatory):
- "resolved": the expected behaviour can be determined from the evidence.
- "unresolved": the situation is test-relevant but its expected outcome CANNOT be determined from the evidence. Set "status": "unresolved" and put the specific open question in "gap_reference". Do NOT invent an expected result, and do NOT write an expected result like "confirm with the product owner" as though it were real product behaviour.
- EVIDENCE-FIRST (mandatory): before classifying a condition as "unresolved", you MUST exhaust the available evidence. Actively search ALL of the analyzed requirements AND all of the business rules below - not only the ones linked to this scenario - for a stated outcome, limit, rule, or state transition that determines the expected behaviour. A condition is "resolved" if its expected outcome follows from any stated requirement or business rule, or from a boundary/equivalence/combination/transition legitimately derived from one (cite it in "rationale" and set the matching "source_basis"). Default to "resolved" whenever the evidence supports a definitive outcome; reach for "unresolved" only after this search genuinely yields nothing.
- Mark "unresolved" ONLY when NO combination of the available requirements and business rules yields a definitive expected outcome. Conservatism is not a reason to mark "unresolved": if the evidence supports a determinate answer, you MUST resolve it. Do not use "unresolved" to avoid the work of deriving the outcome.
- Every "unresolved" condition MUST carry a specific, substantive "gap_reference" naming the exact missing information (what fact, if supplied, would make it resolvable). A vague or empty gap_reference is not acceptable: if you cannot state precisely what is missing, the condition is almost certainly resolvable from the evidence - resolve it.
- Use the GAPS list below to decide unresolved, but apply each gap NARROWLY. A listed gap makes a condition "unresolved" ONLY when the gap's missing information is the very thing that condition verifies - i.e. the gap directly determines the expected result the condition checks. Sharing a requirement with a gap is NOT sufficient. A condition that verifies a DIFFERENT, fully-specified aspect of the same requirement stays "resolved". Examples: (a) if the exact tag copy is unspecified, a condition checking the tag TEXT is unresolved, but a condition checking that the tag APPEARS may be resolved; (b) if the mobile-number FORMAT is unspecified, a condition checking number-format validation is unresolved, but a condition checking that an admin can ADD, PERSIST, or REMOVE a configured number stays resolved because those behaviours are specified independently of the format; (c) if the cron's TIMEZONE is unspecified, a condition checking the timezone is unresolved, but a condition checking that the job runs daily, excludes unconfigured numbers, or repeats nightly stays resolved. Do NOT mark a condition unresolved merely because some gap exists on its requirement - the gap must affect THIS condition's expected outcome.
- Conversely, do NOT ignore a gap that genuinely applies: if the missing information directly affects the expected result this condition verifies, it MUST be "unresolved" with a gap_reference naming that missing information. Narrowing propagation must not become fabricating an outcome the evidence does not support.
- Distinguish an unresolved condition (expected outcome unknown) from a negative condition (expected outcome is known to be a rejection/absence). They are not the same.

Do NOT produce duplicate conditions: two conditions expressing the same proposition with the same parameters are duplicates. Variants with different parameter values (quantity 1 vs 2 vs 3; mapping present vs absent) are NOT duplicates and should each appear when the evidence supports a distinct outcome.

Designed scenarios (with their IDs and requirement links):
$scenarios_json

Analyzed requirements (with their IDs):
$requirements_json

Business rules (with their IDs and requirement links):
$rules_json

Known gaps from requirement analysis (use these to decide unresolved conditions):
$gaps_json

Respond with ONLY this JSON structure, no prose, no markdown fences:

{
  "conditions": [
    {
      "scenario_id": "SC-001",
      "requirement_ids": ["REQ-001"],
      "business_rule_ids": ["BR-001"],
      "category": "combination",
      "description": "Eligible item with an available PLP mapping shows the CTA.",
      "rationale": "Why the evidence justifies this condition.",
      "source_basis": "documented_combination",
      "status": "resolved",
      "parameters": {"eligibility": "eligible", "mapping": "present"},
      "gap_reference": ""
    }
  ]
}
