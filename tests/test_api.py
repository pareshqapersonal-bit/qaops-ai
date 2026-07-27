"""Phase 16 tests: FastAPI backend (ADR-028).

Covers startup, health, model discovery (and secret non-exposure), the design
upload lifecycle (queued -> completed and -> failed), unknown runs, artifact
listing and download, path-traversal rejection, per-run workspace isolation,
and that the API uses the same adaptive execution path as the CLI. LLM calls
are mocked; no test needs a paid provider.
"""

import json
from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from qaops.api.app import create_app
from qaops.api.config import APIConfig
from qaops.core.errors import StageError
from qaops.llm import MockLLMClient

TEST_CASES = json.dumps(
    {
        "test_cases": [
            {
                "scenario_id": "SC-001",
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
DOWNSTREAM = [
    json.dumps({"rules": [{"requirement_id": "REQ-001", "rule": "r", "source_excerpt": ""}]}),
    json.dumps({"gaps": []}),
    json.dumps(
        {
            "scenarios": [
                {
                    "title": "valid login",
                    "description": "d",
                    "category": "positive",
                    "requirement_ids": ["REQ-001"],
                }
            ]
        }
    ),
    TEST_CASES,
]

SCENARIO_CSV = b"title,category,requirement_ids\r\nvalid login,positive,REQ-001\r\n"
REQUIREMENTS_CSV = b"title,description,actors\r\nLogin,Users log in,User\r\n"


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret-key-1234567890")


@pytest.fixture
def config(tmp_path: Path) -> APIConfig:
    return APIConfig(runtime_dir=tmp_path / "runs", cors_origins=["http://localhost:5173"])


@pytest.fixture
def client(config: APIConfig) -> Iterator[TestClient]:
    app = create_app(config)
    with TestClient(app) as test_client:
        yield test_client


def _mock_client(responses: list[str]) -> AbstractContextManager[object]:
    return patch(
        "qaops.services.design_service.create_client",
        return_value=MockLLMClient(list(responses)),
    )


def _submit(client: TestClient, name: str, data: bytes, responses: list[str]) -> str:
    with _mock_client(responses):
        response = client.post("/api/v1/design", files={"file": (name, data, "text/csv")})
    assert response.status_code == 202, response.text
    return str(response.json()["run_id"])


class TestStartup:
    def test_app_builds_and_serves_openapi(self, client: TestClient) -> None:
        assert client.get("/openapi.json").status_code == 200

    def test_docs_available(self, client: TestClient) -> None:
        assert client.get("/docs").status_code == 200


class TestHealth:
    def test_health_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["service"] == "qaops-ai"
        assert body["version"] != "0.1.0"  # real metadata version, not the stale module constant


class TestModels:
    def test_lists_available_providers(self, client: TestClient) -> None:
        response = client.get("/api/v1/models")
        assert response.status_code == 200
        providers = [p["provider"] for p in response.json()["providers"]]
        assert "anthropic" in providers

    def test_no_providers_when_no_keys(
        self, monkeypatch: pytest.MonkeyPatch, client: TestClient
    ) -> None:
        for var in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        assert client.get("/api/v1/models").json()["providers"] == []

    def test_response_contains_no_secret(self, client: TestClient) -> None:
        # The api key is set to a recognisable secret; it must not appear.
        assert "sk-test-secret" not in client.get("/api/v1/models").text

    def test_exposes_capabilities(self, client: TestClient) -> None:
        providers = client.get("/api/v1/models").json()["providers"]
        anthropic = next(p for p in providers if p["provider"] == "anthropic")
        assert anthropic["models"]
        model = anthropic["models"][0]
        assert {"id", "max_context_tokens", "structured_output"} <= set(model)


class TestDesignLifecycle:
    def test_valid_upload_returns_queued(self, client: TestClient) -> None:
        with _mock_client([TEST_CASES]):
            response = client.post(
                "/api/v1/design", files={"file": ("s.csv", SCENARIO_CSV, "text/csv")}
            )
        assert response.status_code == 202
        assert response.json()["status"] == "queued"
        assert response.json()["run_id"].startswith("run_")

    def test_completed_lifecycle_with_summary(self, client: TestClient) -> None:
        run_id = _submit(client, "s.csv", SCENARIO_CSV, [TEST_CASES])
        status = client.get(f"/api/v1/runs/{run_id}").json()
        assert status["status"] == "completed"
        assert status["entry_point"] == "scenarios"
        assert status["summary"]["test_cases"] == 1
        assert status["summary"]["coverage_percent"] == 100.0

    def test_requirements_upload_runs_full_pipeline(self, client: TestClient) -> None:
        run_id = _submit(client, "Requirements.csv", REQUIREMENTS_CSV, DOWNSTREAM)
        status = client.get(f"/api/v1/runs/{run_id}").json()
        assert status["status"] == "completed"
        assert status["entry_point"] == "requirements"

    def test_failed_lifecycle_records_error(self, client: TestClient) -> None:
        # A stage that raises drives the run to failed, not an HTTP error.
        def boom(_settings: object) -> object:
            raise StageError("test_case_generator", "provider exhausted")

        with patch("qaops.services.design_service.create_client", side_effect=boom):
            response = client.post(
                "/api/v1/design", files={"file": ("s.csv", SCENARIO_CSV, "text/csv")}
            )
        run_id = response.json()["run_id"]
        status = client.get(f"/api/v1/runs/{run_id}").json()
        assert status["status"] == "failed"
        assert status["error"]
        assert "Traceback" not in status["error"]

    def test_failed_run_leaks_no_secret(self, client: TestClient) -> None:
        def leak(_settings: object) -> object:
            raise StageError("x", "auth failed with key sk-test-secret-key-1234567890")

        with patch("qaops.services.design_service.create_client", side_effect=leak):
            response = client.post(
                "/api/v1/design", files={"file": ("s.csv", SCENARIO_CSV, "text/csv")}
            )
        run_id = response.json()["run_id"]
        error = client.get(f"/api/v1/runs/{run_id}").json()["error"]
        assert "sk-test-secret" not in error
        assert "[redacted]" in error


class TestInputValidation:
    def test_unsupported_extension_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/design", files={"file": ("notes.rtf", b"hello", "text/plain")}
        )
        assert response.status_code == 400
        assert ".csv" in response.json()["detail"]

    def test_empty_file_rejected(self, client: TestClient) -> None:
        response = client.post("/api/v1/design", files={"file": ("s.csv", b"", "text/csv")})
        assert response.status_code == 400


class TestRunLookup:
    def test_unknown_run_is_404(self, client: TestClient) -> None:
        assert client.get("/api/v1/runs/run_nope").status_code == 404

    def test_unknown_run_artifacts_is_404(self, client: TestClient) -> None:
        assert client.get("/api/v1/runs/run_nope/artifacts").status_code == 404


class TestArtifacts:
    def test_lists_artifacts(self, client: TestClient) -> None:
        run_id = _submit(client, "s.csv", SCENARIO_CSV, [TEST_CASES])
        artifacts = client.get(f"/api/v1/runs/{run_id}/artifacts").json()["artifacts"]
        names = {a["name"] for a in artifacts}
        assert any(n.endswith(".json") for n in names)

    def test_downloads_an_artifact(self, client: TestClient) -> None:
        run_id = _submit(client, "s.csv", SCENARIO_CSV, [TEST_CASES])
        artifacts = client.get(f"/api/v1/runs/{run_id}/artifacts").json()["artifacts"]
        name = next(a["name"] for a in artifacts if a["name"].endswith(".json"))
        response = client.get(f"/api/v1/runs/{run_id}/artifacts/{name}")
        assert response.status_code == 200
        # The JSON report is valid JSON.
        assert json.loads(response.content)

    def test_unknown_artifact_is_404(self, client: TestClient) -> None:
        run_id = _submit(client, "s.csv", SCENARIO_CSV, [TEST_CASES])
        assert client.get(f"/api/v1/runs/{run_id}/artifacts/nope.json").status_code == 404

    def test_path_traversal_is_rejected(self, client: TestClient) -> None:
        run_id = _submit(client, "s.csv", SCENARIO_CSV, [TEST_CASES])
        for attempt in (
            "..%2f..%2fetc%2fpasswd",
            "....//....//etc/passwd",
            "%2e%2e%2fsecrets.txt",
        ):
            response = client.get(f"/api/v1/runs/{run_id}/artifacts/{attempt}")
            assert response.status_code == 404, attempt


class TestWorkspaceIsolation:
    def test_runs_have_separate_workspaces(self, client: TestClient) -> None:
        first = _submit(client, "s.csv", SCENARIO_CSV, [TEST_CASES])
        second = _submit(client, "s.csv", SCENARIO_CSV, [TEST_CASES])
        assert first != second
        store = client.app.state.store  # type: ignore[attr-defined]
        run_a = store.get(first)
        run_b = store.get(second)
        assert run_a.workspace != run_b.workspace
        # Each has its own output; artifacts do not collide.
        assert run_a.output_dir != run_b.output_dir
        assert (run_a.output_dir / "s.json").exists()
        assert (run_b.output_dir / "s.json").exists()


class TestAdaptivePathReuse:
    def test_design_uses_the_design_service(self, client: TestClient) -> None:
        # The API must go through DesignService (which owns the adaptive
        # executor), not a bespoke pipeline path.
        from qaops.services.design_service import DesignService

        original = DesignService.run
        seen: list[str] = []

        def spy(self: DesignService, input_path: Path, *args: object, **kwargs: object) -> object:
            seen.append(input_path.name)
            return original(self, input_path, *args, **kwargs)  # type: ignore[arg-type]

        with patch.object(DesignService, "run", spy), _mock_client([TEST_CASES]):
            response = client.post(
                "/api/v1/design", files={"file": ("s.csv", SCENARIO_CSV, "text/csv")}
            )
        assert response.status_code == 202
        assert seen  # the service.run method was invoked


class TestRunProgress:
    """Phase 16.1: structured progress and bounded-failure info (ADR-029)."""

    def test_progress_present_with_failover(
        self, monkeypatch: pytest.MonkeyPatch, client: TestClient
    ) -> None:
        # Progress is populated from executor events, which run when failover is
        # active (more than one provider available).
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-secret-key-1234567890")
        run_id = _submit(client, "s.csv", SCENARIO_CSV, [TEST_CASES])
        status = client.get(f"/api/v1/runs/{run_id}").json()
        assert status["progress"] is not None
        assert status["progress"]["current_stage"]
        assert status["progress"]["stage_count"] >= 1

    def test_failed_run_exposes_stage_and_recovery(self, client: TestClient) -> None:
        from qaops.core.errors import StageError

        def boom(_settings: object) -> object:
            raise StageError(
                "test_case_generator",
                "Stage recovery budget exhausted after 12 recovery actions",
            )

        with patch("qaops.services.design_service.create_client", side_effect=boom):
            response = client.post(
                "/api/v1/design", files={"file": ("s.csv", SCENARIO_CSV, "text/csv")}
            )
        run_id = response.json()["run_id"]
        status = client.get(f"/api/v1/runs/{run_id}").json()
        assert status["status"] == "failed"
        assert status["failed_stage"] == "test_case_generator"
        assert "recovery budget" in status["error"]

    def test_progress_contains_no_secret(self, client: TestClient) -> None:
        run_id = _submit(client, "s.csv", SCENARIO_CSV, [TEST_CASES])
        assert "sk-test-secret" not in client.get(f"/api/v1/runs/{run_id}").text


class TestInFlightProgress:
    """Phase 16.2: in-flight request visibility and single-provider progress."""

    def test_single_provider_run_has_progress(
        self, monkeypatch: pytest.MonkeyPatch, client: TestClient
    ) -> None:
        # Only Anthropic configured: the run still routes through the executor,
        # so progress is populated (Phase 16.1 gap closed).
        for var in ("OPENROUTER_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        run_id = _submit(client, "s.csv", SCENARIO_CSV, [TEST_CASES])
        status = client.get(f"/api/v1/runs/{run_id}").json()
        assert status["status"] == "completed"
        assert status["progress"] is not None
        assert status["progress"]["current_stage"]

    def test_progress_exposes_request_counters(
        self, monkeypatch: pytest.MonkeyPatch, client: TestClient
    ) -> None:
        run_id = _submit(client, "s.csv", SCENARIO_CSV, [TEST_CASES])
        progress = client.get(f"/api/v1/runs/{run_id}").json()["progress"]
        # The disambiguated counters are present in the schema (section 9, 10).
        assert "model_attempt_number" in progress
        assert "request_attempt" in progress
        assert "provider_call_number" in progress  # actual provider calls (ADR-030)
        assert "models_attempted" in progress  # retained for compatibility
        # provider_call_number is a per-stage count; it is >= 0 and never a
        # hidden multiple. (The final stage, coverage_validator, is deterministic
        # and makes no provider call, so the final snapshot may legitimately
        # show 0 - the field is still present and accurate per stage.)
        assert progress["provider_call_number"] >= 0

    def test_in_flight_request_is_visible(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # Capture run state at the moment a provider request is in flight, by
        # blocking inside the mocked client until the test inspects the run.
        import threading

        from qaops.api.app import create_app
        from qaops.api.config import APIConfig

        release = threading.Event()
        in_flight = threading.Event()

        class BlockingClient:
            provider_name = "anthropic"
            model = "claude-sonnet-4-6"

            def complete(self, request: object) -> object:  # pragma: no cover - timing
                in_flight.set()
                release.wait(timeout=5)
                raise RuntimeError("released")

            def complete_structured(self, *a: object, **k: object) -> object:
                in_flight.set()
                release.wait(timeout=5)
                raise RuntimeError("released")

        cfg = APIConfig(runtime_dir=tmp_path / "runs", cors_origins=["http://localhost:5173"])
        app = create_app(cfg)
        # Run background tasks on a real thread so we can inspect mid-flight.
        with (
            patch("qaops.services.design_service.create_client", return_value=BlockingClient()),
            TestClient(app) as tc,
        ):
            # Fire the request in a thread; TestClient runs background tasks
            # synchronously, so submit in a worker and inspect from the main.
            result: dict[str, str] = {}

            def submit() -> None:
                resp = tc.post(
                    "/api/v1/design",
                    files={"file": ("s.csv", SCENARIO_CSV, "text/csv")},
                )
                result["run_id"] = resp.json()["run_id"]

            worker = threading.Thread(target=submit)
            worker.start()
            try:
                assert in_flight.wait(timeout=5), "request never went in flight"
                # The run exists and is mid-execution; a REQUEST_STARTED
                # event has fired, so progress shows the current model.
                store = app.state.store
                running = [r for r in store.all() if r.execution.current_stage]
                assert running, "no run reported an in-flight stage"
                assert running[0].execution.model == "claude-sonnet-4-6"
                assert running[0].execution.request_attempt >= 1
                # The in-flight call is counted as a real provider call (ADR-030).
                assert running[0].execution.provider_call_number >= 1
            finally:
                release.set()
                worker.join(timeout=5)

    def test_progress_message_has_no_secret(
        self, monkeypatch: pytest.MonkeyPatch, client: TestClient
    ) -> None:
        run_id = _submit(client, "s.csv", SCENARIO_CSV, [TEST_CASES])
        body = client.get(f"/api/v1/runs/{run_id}").text
        assert "sk-test-secret" not in body
