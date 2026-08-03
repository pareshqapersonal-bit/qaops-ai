You are reflecting on how a QA test-design pipeline run executed.

Completed stages: $successes_json
Failed stages: $failures_json
Stages that needed retries: $retries_json
Stages that recovered after retry: $recovered_json
Stages skipped (reused from checkpoints): $skipped_json

Write a concise one-paragraph summary of how the run went, and a short list of
lessons an engineer should take away. Reason only about EXECUTION (successes,
failures, retries, recovery, reuse). Do NOT produce or modify any requirements,
business rules, gaps, scenarios, test conditions, test cases, or coverage.

Respond with ONLY this JSON (no prose, no fences):
{"summary":"<one paragraph>","lessons":["<lesson>", "..."]}
