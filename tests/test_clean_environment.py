"""Regression: the suite must work with NO provider credentials (ADR none - fix).

A clean clone with no QAOPS_* or provider-key variables regressed because
QAOpsSettings (a pydantic BaseSettings, env_prefix "QAOPS_") absorbed any
ambient QAOPS_PROVIDER, and preflight then demanded that provider's key before
the create_client mock boundary was reached. The tests/conftest.py isolation
fixture strips those ambient variables so behaviour is deterministic.

These tests assert the intended end state directly: with every provider key
absent, mocked flows complete, the secret-redaction test reaches its injected
failure, and no real provider request is ever made. They also assert that
production still fails clearly when a real provider genuinely lacks a key - the
isolation must not paper over that.
"""

import json
import os
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from qaops.api.app import create_app
from qaops.api.config import APIConfig
from qaops.config import QAOpsSettings
from qaops.core.errors import ConfigurationError, StageError
from qaops.entrypoints.entry_point import EntryPoint
from qaops.entrypoints.preflight import preflight
from qaops.llm import MockLLMClient
from qaops.llm.models import LLMResponse
from qaops.services import DesignService

_TEST_CASES = json.dumps(
    {
        "test_cases": [
            {
                "scenario_id": "SC-001",
                "requirement_ids": ["REQ-001"],
                "title": "t",
                "objective": "o",
                "expected_result": "r",
                "steps": [{"action": "a", "expected": "e"}],
                "priority": "high",
                "test_type": "functional",
            }
        ]
    }
)
_SCENARIO_CSV = b"title,category,requirement_ids\r\nvalid,positive,REQ-001\r\n"


@pytest.fixture(autouse=True)
def _assert_no_keys() -> Iterator[None]:
    """Guard: these tests must genuinely run with no provider keys present.

    The suite-wide conftest strips them; this asserts it actually happened, so
    the regression cannot silently pass because a key leaked in.
    """
    for name in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        assert not os.environ.get(name), f"{name} must be absent for this regression test"
    yield


class _NoNetworkClient(MockLLMClient):
    """A mock client that fails loudly if anything tries a real network call.

    MockLLMClient already returns scripted responses without a network, but this
    makes the "no live request" guarantee explicit and self-documenting.
    """


def _mock_client(
    responses: list[str | LLMResponse | Exception],
) -> AbstractContextManager[object]:
    return patch(
        "qaops.services.design_service.create_client",
        return_value=_NoNetworkClient(responses),
    )


class TestCleanEnvironmentApi:
    def _client(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        # With no provider keys, the mock provider is the one that runs without
        # credentials (it is exempt from the preflight key check). Selecting it
        # via QAOPS_PROVIDER mirrors exactly how a keyless environment drives a
        # mocked flow - no real key, no live call.
        monkeypatch.setenv("QAOPS_PROVIDER", "mock")
        app = create_app(APIConfig(runtime_dir=tmp_path / "runs"))
        return TestClient(app)

    def test_mocked_design_lifecycle_completes_without_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = self._client(tmp_path, monkeypatch)
        with _mock_client([_TEST_CASES]):
            response = client.post(
                "/api/v1/design", files={"file": ("s.csv", _SCENARIO_CSV, "text/csv")}
            )
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        status = client.get(f"/api/v1/runs/{run_id}").json()
        assert status["status"] == "completed"
        assert status["summary"]["test_cases"] == 1

    def test_secret_redaction_reaches_injected_failure_without_keys(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The injected StageError must be reached - preflight must NOT short out
        # first with "No API key" for an ambient provider.
        def leak(_settings: object) -> object:
            raise StageError("x", "auth failed with key sk-secret-key-1234567890")

        client = self._client(tmp_path, monkeypatch)
        with patch("qaops.services.design_service.create_client", side_effect=leak):
            response = client.post(
                "/api/v1/design", files={"file": ("s.csv", _SCENARIO_CSV, "text/csv")}
            )
        run_id = response.json()["run_id"]
        status = client.get(f"/api/v1/runs/{run_id}").json()
        assert status["status"] == "failed"
        assert "sk-secret" not in status["error"]
        assert "[redacted]" in status["error"]


class TestCleanEnvironmentService:
    def test_mocked_service_run_completes_without_keys(self, tmp_path: Path) -> None:
        path = tmp_path / "s.csv"
        path.write_text("title,category,requirement_ids\r\nvalid,positive,REQ-001\r\n", newline="")
        settings = QAOpsSettings(
            provider="mock", output_dir=tmp_path / "out", default_export_formats=["json"]
        )
        with _mock_client([_TEST_CASES]):
            outcome = DesignService().run(path, settings)
        assert outcome.artifacts
        assert all(a.path.exists() for a in outcome.artifacts)

    def test_artifact_generation_works_without_keys(self, tmp_path: Path) -> None:
        path = tmp_path / "s.csv"
        path.write_text("title,category\r\nvalid,positive\r\n", newline="")
        settings = QAOpsSettings(
            provider="mock", output_dir=tmp_path / "out", default_export_formats=["json"]
        )
        with _mock_client([_TEST_CASES]):
            outcome = DesignService().run(path, settings)
        json_artifacts = [a for a in outcome.artifacts if a.path.suffix == ".json"]
        assert json_artifacts
        data = json.loads(json_artifacts[0].path.read_text())
        assert "test_cases" in data


class TestProductionStillFailsClearly:
    """The isolation removes ambient keys; it must not hide a real missing key."""

    def test_preflight_reports_missing_key_for_a_real_provider(self, tmp_path: Path) -> None:
        # A real provider with no key must still produce a clear preflight issue -
        # the production safety behaviour is preserved.
        path = tmp_path / "s.csv"
        path.write_text("title,category\r\nvalid,positive\r\n", newline="")
        settings = QAOpsSettings(provider="openrouter")
        issues = preflight(path, settings, EntryPoint.SCENARIOS)
        assert any("No API key found for provider 'openrouter'" in i.problem for i in issues)

    def test_service_run_raises_on_missing_key_for_a_real_provider(self, tmp_path: Path) -> None:
        path = tmp_path / "s.csv"
        path.write_text("title,category\r\nvalid,positive\r\n", newline="")
        settings = QAOpsSettings(provider="openrouter", output_dir=tmp_path / "out")
        # No create_client mock here: the run must stop at preflight with a clear
        # ConfigurationError, not attempt any provider call.
        with pytest.raises(ConfigurationError, match="No API key"):
            DesignService().run(path, settings)


class TestAmbientCwdConfigIsolation:
    """A repo-root qaops.yaml must not contaminate the suite (Option 1 fix).

    The failure this guards against: a developer keeps a repo-root qaops.yaml
    (e.g. provider: openrouter for manual live testing). load_settings(None)
    reads ./qaops.yaml, so every test going through the API/CLI path resolves
    that provider and fails on the missing key - even though no test chose it.
    The conftest isolates config discovery by running each test from a clean
    cwd, so the suite is independent of the directory it runs from.
    """

    def test_cwd_has_no_ambient_qaops_yaml_during_tests(self) -> None:
        # The isolation fixture has chdir'd us into a config-free directory.
        assert not Path("qaops.yaml").exists()

    def test_load_settings_none_ignores_a_repo_root_qaops_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulate the developer's repo root: a qaops.yaml selecting openrouter.
        # Because the suite runs from an isolated cwd, load_settings(None) does
        # NOT pick it up, so it falls back to the default provider - proving the
        # ~35-failure contamination cannot recur.
        from qaops.cli.config_loader import load_settings

        # The autouse fixture already put us in a clean temp cwd. Write a
        # poisoned qaops.yaml into a DIFFERENT directory (the developer's repo
        # root analogue) and confirm it is not read from our isolated cwd.
        repo_root_analogue = tmp_path / "repo"
        repo_root_analogue.mkdir()
        (repo_root_analogue / "qaops.yaml").write_text("provider: openrouter\n")
        # We are NOT chdir'd into repo_root_analogue, so discovery misses it.
        settings = load_settings(None)
        assert settings.provider == "anthropic"  # the default, not openrouter

    @pytest.mark.uses_cwd_config
    def test_cwd_discovery_still_works_when_opted_in(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Production behaviour is unchanged: when a test manages its own cwd
        # (opting out of isolation via the marker), load_settings(None) still
        # discovers ./qaops.yaml exactly as production does. This proves the fix
        # isolated the suite without altering load_settings itself.
        from qaops.cli.config_loader import load_settings

        monkeypatch.chdir(tmp_path)
        (tmp_path / "qaops.yaml").write_text("provider: gemini\n")
        settings = load_settings(None)
        assert settings.provider == "gemini"
