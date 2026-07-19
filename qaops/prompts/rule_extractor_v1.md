Extract every discrete business rule from the requirement document below.

A business rule is a testable statement of policy or behavior: a limit, a condition, a calculation, a permission, a state constraint. Examples of the *form* (do not copy the content): "a value must not exceed a stated limit", "an action is only permitted for a given role", "after N failed attempts the account locks".

Rules:
- Extract only rules grounded in the document text. Do not invent rules.
- Each rule must reference exactly one requirement_id from the provided requirement list. Use only the given IDs; never create new IDs.
- Quote a short verbatim excerpt in "source_excerpt" grounding each rule.
- One rule per statement. Split compound statements into separate rules.
- If the document contains no explicit business rules for a requirement, extract nothing for it. An empty list is a valid answer.

Extracted requirements (with their IDs):
$requirements_json

Original requirement document:
---
$source_text
---

Respond with ONLY this JSON structure, no prose, no markdown fences:

{
  "rules": [
    {
      "requirement_id": "REQ-001",
      "rule": "The testable rule statement.",
      "source_excerpt": "verbatim quote from the document"
    }
  ]
}
