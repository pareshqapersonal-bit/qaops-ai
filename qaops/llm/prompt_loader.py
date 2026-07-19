"""Versioned prompt loader (ADR-002, ADR-010).

Prompt templates live as files named ``<name>_<version>.md`` (e.g.
``analyzer_v1.md``) in qaops/prompts/. Templates use string.Template
``$variable`` placeholders - NOT str.format - because prompts routinely
contain JSON examples whose braces would collide with format syntax.

Missing templates and missing variables raise ConfigurationError at
render time: a prompt problem is a configuration problem, caught before
any tokens are spent.
"""

from pathlib import Path
from string import Template

from qaops.core.errors import ConfigurationError

_DEFAULT_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


class PromptLoader:
    """Loads and renders versioned prompt templates from a directory."""

    def __init__(self, base_dir: Path | None = None, version: str = "v1") -> None:
        self._base_dir = base_dir or _DEFAULT_PROMPT_DIR
        if not version or not version.replace("_", "").isalnum():
            msg = f"Invalid prompt version {version!r}"
            raise ConfigurationError(msg)
        self._version = version

    @property
    def version(self) -> str:
        return self._version

    def path_for(self, name: str) -> Path:
        return self._base_dir / f"{name}_{self._version}.md"

    def load(self, name: str) -> str:
        """Return the raw template text for a prompt name."""
        path = self.path_for(name)
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            available = sorted(p.name for p in self._base_dir.glob("*.md")) or ["<none>"]
            msg = f"Prompt template not found: {path}. Available templates: {', '.join(available)}"
            raise ConfigurationError(msg) from exc

    def render(self, name: str, **variables: str) -> str:
        """Load a template and substitute all ``$variable`` placeholders.

        Every placeholder must be supplied; unknown extras are rejected
        so typos in variable names fail loudly instead of silently
        rendering an incomplete prompt.
        """
        template = Template(self.load(name))
        identifiers = set(template.get_identifiers())
        supplied = set(variables)
        missing = identifiers - supplied
        if missing:
            msg = f"Prompt {name!r} ({self._version}) missing variables: {sorted(missing)}"
            raise ConfigurationError(msg)
        extra = supplied - identifiers
        if extra:
            msg = f"Prompt {name!r} ({self._version}) got unknown variables: {sorted(extra)}"
            raise ConfigurationError(msg)
        return template.substitute(**variables)
