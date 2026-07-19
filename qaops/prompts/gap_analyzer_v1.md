Review the requirement document below the way a senior QA engineer reviews it before designing tests: find every ambiguity, missing detail, undefined behavior, and unstated assumption that would block confident test design.

Look for (non-exhaustive):
- Unspecified limits and formats (lengths, ranges, character sets, file sizes)
- Undefined behavior for failures (network errors, timeouts, concurrent actions)
- Missing validation rules for stated inputs
- Undefined states and transitions (what happens after expiry, cancellation, retry)
- Unstated permissions (who may perform the action, what others see)
- Vague quantifiers ("fast", "secure", "user-friendly") with no measurable criterion
- Missing error messages or user feedback definitions

Rules:
- Report only genuine gaps: things the document does NOT define. Never contradict what the document does state.
- Severity: "blocker" if test design for the affected area is impossible without an answer; "major" if tests can be drafted but key paths stay unverifiable; "minor" for polish-level omissions.
- Link each gap to the affected requirement_id from the provided list when one applies; use null for document-wide gaps. Use only the given IDs.
- Phrase "suggested_question" as the exact question you would ask the business analyst or product owner.
- An empty list is a valid answer for a fully specified document.

Extracted requirements (with their IDs):
$requirements_json

Original requirement document:
---
$source_text
---

Respond with ONLY this JSON structure, no prose, no markdown fences:

{
  "gaps": [
    {
      "description": "What is missing or ambiguous.",
      "severity": "blocker | major | minor",
      "requirement_id": "REQ-001 or null",
      "suggested_question": "The question to ask the BA/PO."
    }
  ]
}
