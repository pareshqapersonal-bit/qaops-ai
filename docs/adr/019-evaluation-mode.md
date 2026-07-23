# ADR-019: A temporary evaluation mode, pending document chunking

**Status:** Accepted (temporary — to be superseded) · **Date:** 2026-07-22 · **Relates to:** ADR-001, ADR-006

## Context

A real 7656-character PRD, run end-to-end for the first time, exposed a
genuine limitation: the requirement and business-rule stages generate more
structured JSON than a single model response can return. Runs truncated at
`stop_reason=length` with 31k–38k characters of otherwise excellent output.
Raising `max_output_tokens` helped the analyzer but not the rule extractor,
and every model has a hard output ceiling regardless of what is requested.

The correct fix is document chunking: process the input in batches and merge
results. That is real work and deserves its own release. What was needed
first was the ability to demonstrate the *complete* pipeline on a real PRD.

## Decision

Add a temporary, off-by-default evaluation mode that reduces generation **at
the source** rather than truncating after the fact.

1. **Two settings:** `evaluation_mode` (bool, default `false`) and
   `max_requirements` (int, default 10). Both are accepted in `qaops.yaml`.
2. **When enabled, the analyzer prompt gains one instruction** — "Extract at
   most N requirements" — injected through a `${evaluation_note}` placeholder.
   This is the load-bearing part: capping *after* parsing would not help,
   because the truncation happens during generation. Fewer requirements also
   shrink every downstream stage, since business rules, scenarios, and test
   cases are all generated per requirement.
3. **The cap is re-enforced in code after parsing**, as a safety check, because
   the model may ignore the instruction (ADR-001: the LLM generates,
   deterministic code validates).
4. **When disabled, behavior is byte-identical to before.** The placeholder
   substitutes to an empty string and the rendered prompt matches the original
   exactly — verified by test, including the absence of a stray blank line.

## Consequences

- A real PRD can be processed start to finish, producing genuine coverage
  reports and exports, so output *quality* can finally be judged — the
  outstanding gate for v1.0 (ADR-008).
- The cost is honest and visible: evaluation mode analyzes only part of a large
  document. It is not a scaling solution and must not be presented as one. The
  setting name, the docstrings, and this ADR all say "temporary."
- **This ADR is expected to be superseded.** When chunking lands, evaluation
  mode should be removed, along with the `${evaluation_note}` placeholder and
  both settings. Leaving it in place would be a permanent workaround masking a
  solved problem.
