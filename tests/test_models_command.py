"""Phase 15-rev tests: the `qaops models` diagnostic command (ADR-027)."""

import io
import json

import pytest
from typer.testing import CliRunner

import qaops.cli.app as appmod

runner = CliRunner()


class FakeResponse(io.BytesIO):
    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


OPENROUTER_PAYLOAD = {
    "data": [
        {
            "id": "deepseek/deepseek-chat",
            "context_length": 64000,
            "top_provider": {"max_completion_tokens": 8192},
            "pricing": {"prompt": "0.0000014"},
        }
    ]
}


def test_lists_models_for_available_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    from unittest.mock import patch

    with patch(
        "urllib.request.urlopen",
        return_value=FakeResponse(json.dumps(OPENROUTER_PAYLOAD).encode()),
    ):
        result = runner.invoke(appmod.app, ["models"])
    assert result.exit_code == 0, result.output
    assert "Provider: openrouter" in result.output
    assert "deepseek/deepseek-chat" in result.output


def test_static_flag_skips_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    from unittest.mock import patch

    def explode(*args: object, **kwargs: object) -> object:
        raise AssertionError("discovery must not run with --static")

    with patch("urllib.request.urlopen", side_effect=explode):
        result = runner.invoke(appmod.app, ["models", "--static"])
    assert result.exit_code == 0, result.output
    assert "deepseek/deepseek-chat" in result.output


def test_no_providers_message(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    result = runner.invoke(appmod.app, ["models"])
    assert result.exit_code == 0
    assert "No providers available" in result.output
