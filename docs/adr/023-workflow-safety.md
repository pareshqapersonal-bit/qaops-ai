# ADR-023: Fail safely — never destroy input, never surface raw provider errors

**Status:** Accepted · **Date:** 2026-07-23 · **Relates to:** ADR-016, ADR-017, ADR-022

## Context

Multi-entry input (ADR-022) made a latent hazard concrete. Reports are named
after the input stem, and `csv-bundle` uses fixed filenames, so a run reading
`output/Requirements.csv` writes a fresh `output/Requirements.csv` over its own
input. In practice it "worked" because the read completes first — but a
mid-pipeline failure would have destroyed the source file with no warning.

Separately, expected operational failures were surfacing as raw provider
output. An exhausted OpenRouter balance produced a wall of HTTP 402 JSON inside
a stage error, making a routine, fixable condition look like a crash. And a CSV
left open in Excel produced an unhandled `PermissionError` traceback.

## Decision

Three guards in the CLI layer only. No pipeline stage, prompt, parser,
exporter, or chunking behavior changes.

1. **Refuse to overwrite the input.** Before any write, the CLI computes every
   path it is about to produce — single-file exports named after the input
   stem, plus `CsvBundleExporter.BUNDLE_FILENAMES` — resolves them, and
   compares against the resolved input path. Any collision aborts with an
   explanation and a suggested `--output-dir`. Checking *before* writing means
   a rejected run leaves the directory exactly as it found it, including no
   partial bundle. The bundle filenames became a class constant so the CLI
   reasons about them without duplicating the list.

2. **Classify provider errors.** `diagnose_provider_error` recognises the
   common operational cases — insufficient credit, rate limiting,
   authentication failure, context-length overflow, unavailable model — from
   the provider's own error text and renders a reason plus concrete next
   steps, **always appending the original message** for debugging. Matching is
   text-based deliberately: it works across providers without coupling the CLI
   to any SDK's exception hierarchy, and an unrecognised error degrades to the
   existing raw-text behavior rather than being swallowed.

   Provider failures inside a stage arrive wrapped in `StageError`, so that
   path is diagnosed too — otherwise the raw HTTP body still leaks through the
   stage message, which is exactly how the 402 was first seen.

3. **Translate filesystem errors.** `OSError` during export becomes an
   `ExportError` naming the file and the likely cause; `PermissionError`
   specifically suggests the file is open in another application, which on
   Windows is almost always Excel holding a CSV.

## Consequences

- A user cannot silently lose an input file to a report of the same name. The
  round-trip workflow (export a bundle, edit `Scenarios.csv`, feed it back)
  is now safe by default rather than by convention.
- Expected runtime conditions read as instructions rather than crashes, and
  the original provider text is still there when it is genuinely needed.
- Cost: the collision check is conservative and will reject a run whose input
  merely shares a name with a planned output, even when overwriting would have
  been harmless. Refusing and suggesting `--output-dir` is the safer default.
- Text-based provider classification will miss novel error phrasings and needs
  occasional updating. That failure mode is benign — it falls back to showing
  the raw error, which is what happened before.
