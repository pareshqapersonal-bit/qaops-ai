You are helping a QA engineer turn requirement gaps into a small set of crisp clarification questions for a business analyst or product owner. Each question must capture ONE decision and be answerable with as little typing as possible.

You are given the extracted requirements and a list of gaps. For each gap, decide how to shape it into an answerable question, or skip it.

Rules:
- Shape each gap into ONE clear, self-contained question that a reader can answer without seeing the whole document. Include the concrete subject (e.g. "the store-availability API", "an invalid pincode"), not a vague "the flow".
- Choose the LEAST-typing answer type that fits, in this order of preference:
  - "boolean" for yes/no decisions ("Is the pincode mandatory?", "Should API failure allow retry?").
  - "single_select" when there is a small set of mutually exclusive outcomes; supply "options".
  - "multi_select" when several may apply at once (e.g. which roles); supply "options".
  - "numeric" for a count/limit; "date" for a date.
  - "text" ONLY when the answer genuinely cannot be captured by the above (e.g. an exact error message).
- For select types, provide 2-5 concise, concrete "options". Include an "Other" option only when the set is genuinely open.
- Set "skip": true for a gap when its answer is already stated in the requirements/source, when it is a duplicate of another gap, or when it does not materially affect test coverage. Skipped gaps need no question.
- Do NOT invent requirements or answer the questions yourself. Do NOT restate severity - that is handled elsewhere.
- Reference each shaped question back to its gap by "gap_index" (the 0-based position in the gap list below).

Extracted requirements (with their IDs):
$requirements_json

Gaps to shape (0-based index shown):
$gaps_json

Respond with ONLY this JSON structure, no prose, no markdown fences:

{
  "questions": [
    {
      "gap_index": 0,
      "skip": false,
      "question": "Should the user be allowed to retry when the store-availability API fails?",
      "answer_type": "boolean",
      "options": [],
      "reason": "Retry behavior determines error-path test coverage."
    }
  ]
}
