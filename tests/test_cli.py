"""Phase 7 tests: the CLI layer.

The pipeline's LLM client is replaced with a MockLLMClient by patching
`create_client` in the app module, so the whole command runs offline
(ADR-008). Tests cover the happy path, format/output-dir options,
qaops.yaml loading and precedence, and friendly error handling for the
common failure modes."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import qaops.cli.app as appmod
from qaops.cli.config_loader import load_settings
from qaops.core.errors import ConfigurationError
from qaops.llm import MockLLMClient

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

ANALYZER = json.dumps(
    {
        "requirements": [
            {"title": "Login", "description": "User logs in.", "actors": ["User"]},
            {"title": "Lockout", "description": "Locks after 5."},
        ]
    }
)
RULES = json.dumps(
    {"rules": [{"requirement_id": "REQ-001", "rule": "a rule", "source_excerpt": ""}]}
)
GAPS = json.dumps(
    {
        "gaps": [
            {
                "description": "Lockout duration unspecified.",
                "severity": "blocker",
                "requirement_id": "REQ-002",
                "suggested_question": "How long is the lock?",
            }
        ]
    }
)
SCEN = json.dumps(
    {
        "scenarios": [
            {
                "title": "valid login",
                "description": "d",
                "category": "positive",
                "requirement_ids": ["REQ-001"],
            },
            {
                "title": "lockout",
                "description": "d",
                "category": "boundary_value",
                "requirement_ids": ["REQ-002"],
            },
        ]
    }
)
TCS = json.dumps(
    {
        "test_cases": [
            {
                "scenario_id": "SC-001",
                "requirement_ids": ["REQ-001"],
                "title": "login works",
                "expected_result": "dashboard",
                "steps": [{"action": "login", "expected": "ok"}],
                "priority": "high",
                "test_type": "functional",
            },
            {
                "scenario_id": "SC-002",
                "requirement_ids": ["REQ-002"],
                "title": "lock at fifth attempt",
                "expected_result": "locked",
                "steps": [{"action": "fail 5x", "expected": "locked"}],
                "priority": "critical",
                "test_type": "boundary",
            },
        ]
    }
)

FULL_SCRIPT = [ANALYZER, RULES, GAPS, SCEN, TCS]

runner = CliRunner()


@pytest.fixture
def mock_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the real provider client with a scripted mock for the full run."""

    def fake_create_client(settings: Any) -> MockLLMClient:
        return MockLLMClient(list(FULL_SCRIPT))

    monkeypatch.setattr(appmod, "create_client", fake_create_client)


class TestDesignCommand:
    def test_happy_path_writes_all_default_formats(self, mock_client: None, tmp_path: Path) -> None:
        out = tmp_path / "reports"
        result = runner.invoke(
            appmod.app,
            ["design", str(EXAMPLES_DIR / "login.md"), "-o", str(out), "-f", "json", "-f", "csv"],
        )
        assert result.exit_code == 0, result.output
        assert (out / "login.json").exists()
        assert (out / "login.csv").exists()
        assert "Done." in result.output
        assert "Summary" in result.output

    def test_summary_reports_coverage_and_gaps(self, mock_client: None, tmp_path: Path) -> None:
        result = runner.invoke(
            appmod.app,
            ["design", str(EXAMPLES_DIR / "login.md"), "-o", str(tmp_path), "-f", "json"],
        )
        assert "Requirements:   2" in result.output
        assert "100.0% covered" in result.output
        assert "1 blocker(s)" in result.output

    def test_output_json_round_trips(self, mock_client: None, tmp_path: Path) -> None:
        from qaops.models import TestDesignResult

        runner.invoke(
            appmod.app,
            ["design", str(EXAMPLES_DIR / "login.md"), "-o", str(tmp_path), "-f", "json"],
        )
        loaded = TestDesignResult.model_validate_json((tmp_path / "login.json").read_text())
        assert loaded.source_name == "login.md"
        assert len(loaded.test_cases) == 2

    def test_no_args_shows_help(self) -> None:
        result = runner.invoke(appmod.app, [])
        assert result.exit_code != 0
        assert "design" in result.output.lower()

    def test_pdf_input_runs_end_to_end(self, mock_client: None, tmp_path: Path) -> None:
        from tests.test_ingestion import _make_pdf

        pdf = tmp_path / "spec.pdf"
        _make_pdf(pdf, "User Login\nAccept valid credentials\nLock after five attempts")
        result = runner.invoke(
            appmod.app, ["design", str(pdf), "-o", str(tmp_path / "out"), "-f", "json"]
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / "out" / "spec.json").exists()

    def test_unsupported_input_format_is_friendly(self, mock_client: None, tmp_path: Path) -> None:
        bad = tmp_path / "spec.xlsx"
        bad.write_text("x", encoding="utf-8")
        result = runner.invoke(appmod.app, ["design", str(bad), "-o", str(tmp_path)])
        assert result.exit_code == 1
        assert "Unsupported input format" in result.output
        assert ".pdf" in result.output  # lists supported formats
        assert "Traceback" not in result.output


class TestFriendlyErrors:
    def test_missing_input_file(self, mock_client: None, tmp_path: Path) -> None:
        result = runner.invoke(
            appmod.app, ["design", str(tmp_path / "nope.md"), "-o", str(tmp_path)]
        )
        assert result.exit_code == 1
        assert "Input file not found" in result.output
        assert "Traceback" not in result.output  # friendly, not a stack trace

    def test_unknown_format(self, mock_client: None, tmp_path: Path) -> None:
        result = runner.invoke(
            appmod.app,
            ["design", str(EXAMPLES_DIR / "login.md"), "-o", str(tmp_path), "-f", "pdf"],
        )
        assert result.exit_code == 1
        assert "Unknown export format" in result.output
        assert "Traceback" not in result.output

    def test_oversized_input_is_friendly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(appmod, "create_client", lambda s: MockLLMClient(list(FULL_SCRIPT)))
        big = tmp_path / "big.md"
        big.write_text("x" * 1500)
        cfg = tmp_path / "qaops.yaml"
        cfg.write_text("max_input_chars: 1000\n")  # valid (>=1000) but below input size
        result = runner.invoke(
            appmod.app, ["design", str(big), "-o", str(tmp_path), "-c", str(cfg), "-f", "json"]
        )
        assert result.exit_code == 1
        assert "limit is 1000" in result.output
        assert "Traceback" not in result.output

    def test_debug_flag_reraises(self, mock_client: None, tmp_path: Path) -> None:
        result = runner.invoke(
            appmod.app, ["design", str(tmp_path / "nope.md"), "-o", str(tmp_path), "--debug"]
        )
        assert result.exit_code != 0
        assert result.exception is not None  # raised, not swallowed


class TestConfigLoader:
    def test_defaults_when_no_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)  # no qaops.yaml here
        settings = load_settings(None)
        assert settings.provider == "anthropic"

    def test_reads_qaops_yaml(self, tmp_path: Path) -> None:
        cfg = tmp_path / "qaops.yaml"
        cfg.write_text("provider: gemini\ntemperature: 0.5\ndefault_export_formats: [json, csv]\n")
        settings = load_settings(cfg)
        assert settings.provider == "gemini"
        assert settings.temperature == 0.5
        assert settings.default_export_formats == ["json", "csv"]

    def test_env_overrides_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = tmp_path / "qaops.yaml"
        cfg.write_text("temperature: 0.5\n")
        monkeypatch.setenv("QAOPS_TEMPERATURE", "0.9")
        settings = load_settings(cfg)
        assert settings.temperature == 0.9  # env wins over file

    def test_unknown_key_rejected(self, tmp_path: Path) -> None:
        cfg = tmp_path / "qaops.yaml"
        cfg.write_text("privder: anthropic\n")  # typo
        with pytest.raises(ConfigurationError, match="Unknown key"):
            load_settings(cfg)

    def test_explicit_missing_file_errors(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="not found"):
            load_settings(tmp_path / "absent.yaml")

    def test_invalid_yaml_errors(self, tmp_path: Path) -> None:
        cfg = tmp_path / "qaops.yaml"
        cfg.write_text("provider: [unclosed\n")
        with pytest.raises(ConfigurationError, match="Invalid YAML"):
            load_settings(cfg)

    def test_non_mapping_top_level_errors(self, tmp_path: Path) -> None:
        cfg = tmp_path / "qaops.yaml"
        cfg.write_text("- just\n- a\n- list\n")
        with pytest.raises(ConfigurationError, match="mapping at the top level"):
            load_settings(cfg)

    def test_invalid_value_rejected(self, tmp_path: Path) -> None:
        cfg = tmp_path / "qaops.yaml"
        cfg.write_text("provider: openai\n")  # not in whitelist
        with pytest.raises(ConfigurationError, match="Invalid configuration"):
            load_settings(cfg)
