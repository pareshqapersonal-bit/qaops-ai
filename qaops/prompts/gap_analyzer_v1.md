Review the requirement document below the way an experienced MANUAL QA ANALYST reviews it before designing test cases. Your single guiding question is:

"Do I have enough information to manually verify every affected scope, expected behavior, UI/UX behavior, navigation, data state, negative case, integration, and relevant edge case WITHOUT making assumptions?"

Report only information that is genuinely missing and that materially prevents a manual tester from writing complete, meaningful test coverage. You are clarifying WHAT must be verified and WHAT the expected result is - never HOW the feature should be built or how automation should locate an element.

Consider these coverage areas, but ONLY where they actually apply to the requirement (non-exhaustive):

A. Functional behavior - the expected result of the primary action; behavior for valid input, invalid input, and missing required input; behavior after success and after failure; repeat/retry behavior; alternate flows.

B. UI / UX (only when the requirement changes UI or UX) - whether the affected component/screen/section is clearly identifiable; whether the required UI elements are defined; expected interaction behavior; selected/unselected, enabled/disabled, loading, empty, and error states; visual behavior that cannot otherwise be determined. When the requirement is explicitly a visual/design change, the gap is usually whether an approved design/reference exists to validate against - not the visual values themselves.

C. Responsive / device (only when relevant) - mobile, tablet, desktop, supported device classes, responsive layout, viewport-specific behavior. Do not raise responsive gaps when the requirement or context clearly makes them irrelevant.

D. Navigation / user journey - where the user lands after the action; back navigation; redirects; screen-to-screen behavior; impact on the surrounding journey; cross-screen dependencies.

E. Data / content states (only when relevant) - zero records, one record, multiple records, missing data, invalid data, long content, boundary conditions.

F. Error / negative behavior (only when relevant) - service/API failure, unavailable data, validation failure, the user-visible error state, and retry/recovery behavior.

G. Business rules - eligibility, conditions, limits, permissions, validation rules, user types, state transitions, dependencies.

H. Integration / dependencies (only when relevant) - dependency behavior, downstream impact, dependency failure, recovery, interaction with existing features.

Materiality rule (important - do not over-question):
- A missing detail is a gap ONLY IF (1) it materially affects expected behavior or QA coverage, AND (2) a manual tester cannot reasonably derive it from the document, an approved design/reference, supplied evidence, or surrounding context.
- The goal is COMPLETE manual-QA coverage, NOT the maximum number of gaps. If the requirement is already sufficiently testable, return no gap for it. An empty list is a valid, correct answer for a fully specified requirement.
- Do not ask the user to describe something already clearly stated in the requirement.

Do NOT raise implementation or automation-location gaps. Unless the requirement itself makes the value part of the acceptance criteria, never treat as a gap: CSS selectors, DOM selectors, data-testid, DOM attributes, CSS class names, z-index, CSS properties/values, exact padding/margins, internal component structure, or automation locators. For example, asking which page Trending Searches should appear on is a valid QA gap; asking what CSS selector identifies it is not. Asking whether Proceed to Checkout stays visible while scrolling is valid; asking what z-index it uses is not.

Rules:
- Report only genuine gaps: things the document does NOT define. Never contradict what the document does state.
- Severity: "blocker" if test design for the affected area is impossible without an answer; "major" if tests can be drafted but key paths stay unverifiable; "minor" for polish-level omissions.
- Link each gap to the affected requirement_id from the provided list when one applies; use null for document-wide gaps. Use only the given IDs.
- Phrase "suggested_question" as the exact question you would ask the business analyst or product owner. Prefer questions a reader can answer with yes/no or a short choice, and that translate directly into a manual test scenario.
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
