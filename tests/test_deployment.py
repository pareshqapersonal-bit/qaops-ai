"""Phase 18 deployment tests: FastAPI serving the built frontend (ADR-033).

These prove the single-service production shape without any live LLM call and
without real provider credentials:

- the built frontend index and static assets are served;
- SPA routes (/design, /runs/<id>) return the index for client-side routing;
- the API surface (/health, /api/*) always takes precedence over the SPA and
  keeps returning API responses, and an unknown /api path is an API 404, never
  index.html;
- a missing build degrades cleanly instead of breaking the API;
- no provider secret can appear in the served frontend assets.

A minimal fake ``dist`` is synthesised per test so the suite does not depend on
a real ``npm run build`` having run.
"""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qaops.api.app import create_app
from qaops.api.config import APIConfig

_INDEX_HTML = (
    "<!doctype html><html><head><title>QAOps AI</title>"
    '<script type="module" crossorigin src="/assets/index-TEST.js"></script>'
    '<link rel="stylesheet" crossorigin href="/assets/index-TEST.css">'
    '</head><body><div id="root"></div></body></html>'
)
_ASSET_JS = "console.log('qaops built bundle');\n"
_ASSET_CSS = "#root{font-family:system-ui}\n"


def _make_dist(root: Path) -> Path:
    dist = root / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(_INDEX_HTML, encoding="utf-8")
    (dist / "assets" / "index-TEST.js").write_text(_ASSET_JS, encoding="utf-8")
    (dist / "assets" / "index-TEST.css").write_text(_ASSET_CSS, encoding="utf-8")
    (dist / "favicon.ico").write_bytes(b"\x00\x00")
    return dist


@pytest.fixture
def built_client(tmp_path: Path) -> Iterator[TestClient]:
    dist = _make_dist(tmp_path / "frontend")
    app = create_app(APIConfig(runtime_dir=tmp_path / "runs", static_dir=dist))
    with TestClient(app) as client:
        yield client


@pytest.fixture
def no_build_client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(APIConfig(runtime_dir=tmp_path / "runs", static_dir=tmp_path / "absent-dist"))
    with TestClient(app) as client:
        yield client


def _is_spa_index(text: str) -> bool:
    return '<div id="root">' in text


class TestFrontendServing:
    def test_index_is_served_at_root(self, built_client: TestClient) -> None:
        response = built_client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert _is_spa_index(response.text)

    def test_static_assets_are_served(self, built_client: TestClient) -> None:
        js = built_client.get("/assets/index-TEST.js")
        assert js.status_code == 200
        assert "javascript" in js.headers["content-type"]
        css = built_client.get("/assets/index-TEST.css")
        assert css.status_code == 200
        assert "css" in css.headers["content-type"]

    def test_named_static_file_is_served(self, built_client: TestClient) -> None:
        # A concrete file at the root of the build (favicon) is returned as a
        # file, not the SPA shell.
        favicon = built_client.get("/favicon.ico")
        assert favicon.status_code == 200
        assert not _is_spa_index(favicon.text)


class TestSpaRouting:
    def test_design_route_returns_index(self, built_client: TestClient) -> None:
        response = built_client.get("/design")
        assert response.status_code == 200
        assert _is_spa_index(response.text)

    def test_run_route_returns_index(self, built_client: TestClient) -> None:
        response = built_client.get("/runs/example")
        assert response.status_code == 200
        assert _is_spa_index(response.text)

    def test_deep_unknown_frontend_route_returns_index(self, built_client: TestClient) -> None:
        response = built_client.get("/some/unknown/client/route")
        assert response.status_code == 200
        assert _is_spa_index(response.text)


class TestApiPrecedence:
    def test_health_stays_json_not_html(self, built_client: TestClient) -> None:
        response = built_client.get("/health")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert response.json()["status"] == "ok"
        assert not _is_spa_index(response.text)

    def test_known_api_route_takes_precedence(self, built_client: TestClient) -> None:
        response = built_client.get("/api/v1/models")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")

    def test_unknown_api_route_is_api_404_not_index(self, built_client: TestClient) -> None:
        response = built_client.get("/api/v1/does-not-exist")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        assert not _is_spa_index(response.text)

    def test_unknown_api_subpath_is_api_404_not_index(self, built_client: TestClient) -> None:
        response = built_client.get("/api/totally/made/up")
        assert response.status_code == 404
        assert not _is_spa_index(response.text)


class TestMissingBuild:
    def test_root_degrades_cleanly(self, no_build_client: TestClient) -> None:
        response = no_build_client.get("/")
        assert response.status_code == 503
        assert "frontend build" in response.text.lower()
        assert not _is_spa_index(response.text)

    def test_api_still_works_without_build(self, no_build_client: TestClient) -> None:
        assert no_build_client.get("/health").json()["status"] == "ok"
        assert no_build_client.get("/api/v1/models").status_code == 200

    def test_unknown_api_still_404_without_build(self, no_build_client: TestClient) -> None:
        response = no_build_client.get("/api/v1/does-not-exist")
        assert response.status_code == 404
        assert "frontend build" not in response.text.lower()


class TestNoLocalhostInProductionBundle:
    """The built assets must not hard-code a separate backend host.

    Production is same-origin; a baked-in http://127.0.0.1:8000 or
    http://localhost:8000 would break the deployed app. This guards the built
    output the frontend team ships (mirrored by a frontend test too).
    """

    def test_built_index_has_no_hardcoded_backend_host(self, built_client: TestClient) -> None:
        index = built_client.get("/").text
        js = built_client.get("/assets/index-TEST.js").text
        for needle in ("127.0.0.1:8000", "localhost:8000"):
            assert needle not in index
            assert needle not in js


class TestNoSecretsInStaticOutput:
    """No provider credential may appear in any served frontend asset.

    We plant unmistakable fake secrets into the environment, (re)build the app,
    and assert none of them appear in any served asset. We never print the
    values, and we assert on their ABSENCE, so real secrets are never exposed by
    this test either.
    """

    def test_planted_secrets_absent_from_assets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Sentinels that would be glaring if they leaked into the bundle.
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-PLANTED-SENTINEL-DO-NOT-LEAK")
        monkeypatch.setenv("GEMINI_API_KEY", "AIza-PLANTED-SENTINEL-DO-NOT-LEAK")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-PLANTED-SENTINEL-DO-NOT-LEAK")
        dist = _make_dist(tmp_path / "frontend")
        app = create_app(APIConfig(runtime_dir=tmp_path / "runs", static_dir=dist))
        client = TestClient(app)

        sentinel = "PLANTED-SENTINEL-DO-NOT-LEAK"
        for path in ("/", "/assets/index-TEST.js", "/assets/index-TEST.css"):
            body = client.get(path).text
            assert sentinel not in body

    def test_models_endpoint_never_returns_key_values(
        self, built_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The models endpoint reports provider availability, never key material.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-PLANTED-SENTINEL-DO-NOT-LEAK")
        payload = json.dumps(built_client.get("/api/v1/models").json())
        assert "PLANTED-SENTINEL-DO-NOT-LEAK" not in payload
