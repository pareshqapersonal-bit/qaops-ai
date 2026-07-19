"""Versioned prompt templates.

Naming convention: ``<name>_<version>.md`` (e.g. ``analyzer_v1.md``),
loaded by qaops.llm.PromptLoader. Templates use string.Template
``$variable`` placeholders. Released versions are never edited in
place - see CONTRIBUTING.md. Phase 2 ships the first templates.
"""
