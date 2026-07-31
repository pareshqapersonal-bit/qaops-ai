"""Phase 12.1 tests: workflow safety and CLI hardening (ADR-023).

Covers the input/output collision guard (including that the input survives an
aborted run), provider-error classification, and friendly filesystem-error
handling. No pipeline, prompt, parser, exporter, or chunking change is made by
this phase, so the rest of the suite is the regression check."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import qaops.cli.app as appmod
from qaops.cli.diagnostics import diagnose_provider_error
from qaops.core.errors import ExportError, LLMError, StageError
from qaops.exporters import CsvBundleExporter
from qaops.llm import MockLLMClient
from qaops.services.design_service import _friendly_write_error

TEST_CASES = json.dumps(
    {
        "test_cases": [
            {
                "scenario_id": "SC-001",
                "condition_id": "COND-001",
                "requirement_ids": ["REQ-001"],
                "title": "login works",
                "expected_result": "dashboard",
                "steps": [{"action": "submit", "expected": "ok"}],
                "priority": "high",
                "test_type": "functional",
            }
        ]
    }
)
CONDITIONS = json.dumps(
    {
        "conditions": [
            {
                "scenario_id": "SC-001",
                "requirement_ids": ["REQ-001"],
                "business_rule_ids": [],
                "category": "positive",
                "description": "primary condition",
                "rationale": "REQ-001",
                "source_basis": "explicit_requirement",
                "status": "resolved",
                "parameters": {},
                "gap_reference": "",
            }
        ]
    }
)

SCENARIO_CSV = "title,description,category,requirement_ids\r\nvalid login,d,positive,REQ-001\r\n"

runner = CliRunner()


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preflight checks for a provider key before the (mocked) client is built."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


@pytest.fixture
def mock_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "qaops.services.design_service.create_client",
        lambda settings: MockLLMClient([CONDITIONS, TEST_CASES]),
    )


class TestBundleFilenameConstant:
    def test_constant_matches_what_export_bundle_writes(self, tmp_path: Path) -> None:
        # Guards against the constant drifting from the actual writes.
        from qaops.models import (
            Requirement,
            RequirementAnalysisResult,
            ScenarioDesignResult,
            TestDesignResult,
        )

        result = TestDesignResult(
            source_name="x",
            requirements=[Requirement(id="REQ-001", title="A", description="d")],
        )
        assert isinstance(
            ScenarioDesignResult(
                analysis=RequirementAnalysisResult(source_name="x", source_text="t")
            ),
            ScenarioDesignResult,
        )
        CsvBundleExporter().export_bundle(result, tmp_path)
        written = {p.name for p in tmp_path.glob("*.csv")}
        assert written == set(CsvBundleExporter.BUNDLE_FILENAMES)


class TestOutputCollisionGuard:
    def test_bundle_refuses_to_overwrite_its_input(self, mock_client: None, tmp_path: Path) -> None:
        source = tmp_path / "Scenarios.csv"
        source.write_text(SCENARIO_CSV, newline="")
        result = runner.invoke(
            appmod.app,
            ["design", str(source), "--from", "scenarios", "-o", str(tmp_path), "-f", "csv-bundle"],
        )
        assert result.exit_code == 1
        assert "contains the input file" in result.output
        assert "Traceback" not in result.output

    def test_input_file_is_unchanged_after_an_aborted_run(
        self, mock_client: None, tmp_path: Path
    ) -> None:
        source = tmp_path / "Scenarios.csv"
        source.write_text(SCENARIO_CSV, newline="")
        original = source.read_bytes()
        runner.invoke(
            appmod.app,
            ["design", str(source), "--from", "scenarios", "-o", str(tmp_path), "-f", "csv-bundle"],
        )
        assert source.read_bytes() == original

    def test_no_partial_bundle_is_written_when_aborting(
        self, mock_client: None, tmp_path: Path
    ) -> None:
        source = tmp_path / "Scenarios.csv"
        source.write_text(SCENARIO_CSV, newline="")
        runner.invoke(
            appmod.app,
            ["design", str(source), "--from", "scenarios", "-o", str(tmp_path), "-f", "csv-bundle"],
        )
        # The check happens before any write, so no other bundle file appears.
        assert not (tmp_path / "TestCases.csv").exists()
        assert not (tmp_path / "Coverage.csv").exists()

    def test_single_file_exporter_refuses_to_overwrite_input(
        self, mock_client: None, tmp_path: Path
    ) -> None:
        # Input named scen.json; the json exporter would write scen.json.
        source = tmp_path / "scen.json"
        source.write_text(json.dumps([{"title": "valid login", "category": "positive"}]))
        original = source.read_bytes()
        result = runner.invoke(
            appmod.app,
            ["design", str(source), "--from", "scenarios", "-o", str(tmp_path), "-f", "json"],
        )
        assert result.exit_code == 1
        assert "contains the input file" in result.output
        assert source.read_bytes() == original

    def test_different_output_directory_is_allowed(self, mock_client: None, tmp_path: Path) -> None:
        source = tmp_path / "Scenarios.csv"
        source.write_text(SCENARIO_CSV, newline="")
        original = source.read_bytes()
        out = tmp_path / "run2"
        result = runner.invoke(
            appmod.app,
            ["design", str(source), "--from", "scenarios", "-o", str(out), "-f", "csv-bundle"],
        )
        assert result.exit_code == 0, result.output
        assert (out / "Scenarios.csv").exists()
        assert source.read_bytes() == original  # input untouched

    def test_unrelated_input_name_is_allowed(self, mock_client: None, tmp_path: Path) -> None:
        # A document-route input in the output dir whose name clashes with
        # nothing the bundle writes is fine.
        source = tmp_path / "my_scenarios.csv"
        source.write_text(SCENARIO_CSV, newline="")
        result = runner.invoke(
            appmod.app,
            ["design", str(source), "--from", "scenarios", "-o", str(tmp_path), "-f", "csv-bundle"],
        )
        assert result.exit_code == 0, result.output


class TestProviderDiagnosis:
    def test_insufficient_credit(self) -> None:
        diagnosis = diagnose_provider_error(
            "Error code: 402 - requires more credits, you requested up to 16384 tokens "
            "but can only afford 15461"
        )
        assert diagnosis is not None
        assert "insufficient credit" in diagnosis.reason.casefold()
        assert any("max_output_tokens" in a for a in diagnosis.actions)

    def test_rate_limited(self) -> None:
        diagnosis = diagnose_provider_error(
            "Error code: 429 - gemma-4-31b:free is temporarily rate-limited upstream"
        )
        assert diagnosis is not None
        assert "rate-limiting" in diagnosis.reason

    def test_authentication_failure(self) -> None:
        diagnosis = diagnose_provider_error(
            "Error code: 401 - {'type': 'authentication_error', 'message': 'invalid x-api-key'}"
        )
        assert diagnosis is not None
        assert "API key" in diagnosis.reason

    def test_model_unavailable(self) -> None:
        diagnosis = diagnose_provider_error("Error code: 404 - This model is unavailable for free.")
        assert diagnosis is not None
        assert "unavailable" in diagnosis.reason

    def test_unrecognised_error_returns_none(self) -> None:
        assert diagnose_provider_error("something entirely novel happened") is None

    def test_render_preserves_the_original_error(self) -> None:
        raw = "Error code: 402 - requires more credits"
        diagnosis = diagnose_provider_error(raw)
        assert diagnosis is not None
        rendered = diagnosis.render(raw)
        assert "Reason:" in rendered
        assert "Suggested actions:" in rendered
        assert raw in rendered  # original preserved for debugging


class TestFriendlyErrorMessages:
    def test_wrapped_provider_error_in_a_stage_is_diagnosed(self) -> None:
        exc = StageError(
            "test_case_generator",
            "[openrouter] Error code: 402 - requires more credits, can only afford 15461",
        )
        message = appmod._message_for(exc)
        assert "The AI provider call failed." in message
        assert "Suggested actions:" in message
        assert "Reduce max_output_tokens" in message

    def test_unrecognised_stage_error_keeps_its_message(self) -> None:
        exc = StageError("scenario_generator", "Model generated duplicate scenarios")
        message = appmod._message_for(exc)
        assert message.startswith("A pipeline stage failed.")
        assert "duplicate scenarios" in message

    def test_direct_llm_error_is_diagnosed(self) -> None:
        message = appmod._message_for(LLMError("Error code: 429 - rate-limited upstream"))
        assert "Suggested actions:" in message


class TestFilesystemErrors:
    def test_permission_error_is_friendly(self) -> None:
        error = _friendly_write_error(
            Path("output/TestCases.csv"), PermissionError(13, "Permission denied")
        )
        assert isinstance(error, ExportError)
        assert "open in another application" in str(error)
        assert "TestCases.csv" in str(error)

    def test_is_a_directory_error_is_specific(self) -> None:
        error = _friendly_write_error(
            Path("output/report.json"), IsADirectoryError(21, "Is a directory")
        )
        assert "a directory already exists" in str(error)

    def test_other_os_errors_include_the_cause(self) -> None:
        error = _friendly_write_error(
            Path("output/report.json"), OSError(28, "No space left on device")
        )
        assert "No space left" in str(error)

    def test_locked_file_during_export_is_friendly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            "qaops.services.design_service.create_client",
            lambda s: MockLLMClient([CONDITIONS, TEST_CASES]),
        )
        source = tmp_path / "scen.csv"
        source.write_text(SCENARIO_CSV, newline="")

        def locked_export(self: object, result: object, output_path: str) -> str:
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr("qaops.exporters.json_exporter.JsonExporter.export", locked_export)
        result = runner.invoke(
            appmod.app,
            ["design", str(source), "--from", "scenarios", "-o", str(tmp_path / "o"), "-f", "json"],
        )
        assert result.exit_code == 1
        assert "open in another application" in result.output
        assert "Traceback" not in result.output
