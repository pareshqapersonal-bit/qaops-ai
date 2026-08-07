# Phase 29 - Capturing a real pre-linkage trace

Purpose: prove, from a live execution, whether `_apply_gap_linkage` (the
deterministic backstop) flips any condition from `resolved` to `unresolved`. This
captures the model's raw classification BEFORE the backstop runs, so it can be
compared against the final artifact.

## How to run

Set the `QAOPS_PRELINKAGE_TRACE` environment variable to a writable file path,
then run the pipeline on the PRD exactly as before:

```
QAOPS_PRELINKAGE_TRACE=/path/to/prelinkage.json  <your normal run command for the Auto-Delete PRD>
```

The variable is read inside `TestConditionAnalyzer`. When set, the stage writes a
JSON array to that path - one entry per condition, with the status the model
emitted (before gap linkage), plus `source_basis`, requirement/rule ids,
description, and gap_reference. It also logs a one-line summary
(`pre_linkage_snapshot total=.. resolved=.. unresolved=..`). When the variable is
unset (normal operation) the capture is a complete no-op.

## How to read it

Compare the pre-linkage snapshot against the final run artifact
(`...Final.json`):

- Find `COND-001`, `COND-002`, `COND-003` in the snapshot and note their
  `status`.
- Find the same ids in the final artifact and note their `status`.

Interpretation:

- If the snapshot shows them **resolved** and the final artifact shows them
  **unresolved**, then `_apply_gap_linkage` flipped them - concrete proof the
  deterministic backstop caused the false positives, justifying a targeted
  `_blocking_gap` change (ADR-037 amendment).
- If the snapshot already shows them **unresolved**, the model classified them
  that way and the fix remains a prompt problem - no deterministic change.

This diagnostic changes no behaviour; it only observes. Remove the env var to
return to normal operation.
