"""qaops.yaml loading, layered under the existing settings system.

The CLI does not invent a config system. It reads qaops.yaml into a dict
and hands it to QAOpsSettings as init values, so pydantic-settings keeps
its precedence (environment variables still override file values) and all
existing validation applies unchanged (ADR-017). No settings model is
modified.

File resolution: an explicit --config path, else qaops.yaml in the
current directory if present, else built-in defaults.
"""

import os
from pathlib import Path
from typing import Any

import yaml

from qaops.config import QAOpsSettings
from qaops.core.errors import ConfigurationError

DEFAULT_CONFIG_NAME = "qaops.yaml"

# Keys accepted from qaops.yaml, mapped to QAOpsSettings fields. Unknown keys
# are rejected so a typo fails loudly instead of being silently ignored.
_ALLOWED_KEYS = {
    "provider",
    "model",
    "gemini_model",
    "openrouter_model",
    "temperature",
    "max_output_tokens",
    "llm_retries",
    "max_input_chars",
    "evaluation_mode",
    "max_requirements",
    "prompt_version",
    "output_dir",
    "default_export_formats",
}


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"Could not read config file {path}: {exc}"
        raise ConfigurationError(msg) from exc
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        msg = f"Invalid YAML in {path}: {exc}"
        raise ConfigurationError(msg) from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        msg = (
            f"Config file {path} must contain a mapping at the top level, "
            f"got {type(loaded).__name__}."
        )
        raise ConfigurationError(msg)
    return loaded


def load_settings(config_path: Path | None = None) -> QAOpsSettings:
    """Build QAOpsSettings from qaops.yaml (if any) plus environment.

    Args:
        config_path: explicit path; if None, uses ./qaops.yaml when present.

    Raises:
        ConfigurationError: file missing (when explicit), unreadable,
            invalid YAML, unknown keys, or values failing settings
            validation.
    """
    if config_path is not None:
        if not config_path.exists():
            msg = f"Config file not found: {config_path}"
            raise ConfigurationError(msg)
        file_values = _read_yaml(config_path)
    else:
        default = Path(DEFAULT_CONFIG_NAME)
        file_values = _read_yaml(default) if default.exists() else {}

    unknown = sorted(set(file_values) - _ALLOWED_KEYS)
    if unknown:
        msg = f"Unknown key(s) in config: {unknown}. Allowed keys: {sorted(_ALLOWED_KEYS)}."
        raise ConfigurationError(msg)

    # Precedence: environment (QAOPS_*) must win over qaops.yaml. Passing a
    # value as an init kwarg would make it beat the environment (init args are
    # pydantic-settings' highest precedence), so drop any file key that is
    # already provided via its QAOPS_ environment variable.
    effective = {
        key: value for key, value in file_values.items() if f"QAOPS_{key.upper()}" not in os.environ
    }

    try:
        return QAOpsSettings(**effective)
    except ValueError as exc:
        msg = f"Invalid configuration: {exc}"
        raise ConfigurationError(msg) from exc
