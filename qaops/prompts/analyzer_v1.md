Analyze the following software requirement document and extract every distinct requirement.

Rules:
- Extract only what is grounded in the text. Do not invent features, validations, or constraints that are not stated.
- One requirement per distinct capability or behavior. Do not merge unrelated behaviors into one requirement.
- For each requirement, quote a short verbatim excerpt from the document in "source_excerpt" that grounds it.
- Lists (actors, inputs, outputs, validations, dependencies, constraints, assumptions) may be empty if the text says nothing about them. Record only stated assumptions, not your own.
- Do not report ambiguities or missing details here; that is a separate analysis.

Requirement document:
---
$requirement_text
---

Respond with ONLY this JSON structure, no prose, no markdown fences:

{
  "requirements": [
    {
      "title": "Short requirement name",
      "description": "What the system must do, in one or two sentences.",
      "source_excerpt": "verbatim quote from the document",
      "actors": ["..."],
      "inputs": ["..."],
      "outputs": ["..."],
      "validations": ["..."],
      "dependencies": ["..."],
      "constraints": ["..."],
      "assumptions": ["..."]
    }
  ]
}
