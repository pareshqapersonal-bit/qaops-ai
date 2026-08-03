You are explaining an execution plan for a QA test-design pipeline.

The goal is: $goal
The pipeline entry point is: $entry_point
The stages that will run, in fixed order, are: $stages_json

For EACH stage in that exact list, write one short sentence explaining why it is
required and what it contributes to the goal. Do not add, remove, or reorder
stages. Do not invent any requirements, scenarios, test cases, or other pipeline
content - only explain the execution.

Respond with ONLY this JSON (no prose, no fences):
{"steps":[{"stage":"<stage name from the list>","reason":"<why it runs>"}]}
