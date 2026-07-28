"""Test-suite isolation from the ambient environment.

QAOpsSettings is a pydantic BaseSettings with env_prefix "QAOPS_", so any
QAOPS_* variable in the developer's shell (e.g. QAOPS_PROVIDER=openrouter) leaks
into every QAOpsSettings() a test constructs. Likewise provider API-key
variables (ANTHROPIC_API_KEY, OPENROUTER_API_KEY, GEMINI_API_KEY,
GOOGLE_API_KEY) are read directly from os.environ by preflight and the provider
clients.

Without isolation the suite's result depends on the machine it runs on: a clean
CI box passes while a developer who has QAOPS_PROVIDER or a provider key exported
sees dozens of failures (preflight demands a key for a provider the test never
chose, before the test's create_client mock is ever reached).

This autouse fixture strips those variables before every test so the suite runs
against a known-clean baseline. Tests that need a specific provider or key set
it explicitly via monkeypatch.setenv, which still works because this fixture
runs first and only removes pre-existing ambient values. Production is
unaffected: it does not load this conftest.

This never ADDS a credential - it only removes ambient ones - so it cannot mask
a real "missing key" failure or enable a live provider call.
"""

import os
from collections.abc import Iterator

import pytest

# Every QAOPS_-prefixed setting that could leak into QAOpsSettings(), plus the
# provider credential variables read straight from the environment.
_QAOPS_PREFIX = "QAOPS_"
_PROVIDER_KEY_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
)


@pytest.fixture(autouse=True)
def _isolate_cwd_config(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Run each test from a directory with no ambient ``qaops.yaml``.

    ``load_settings(None)`` resolves configuration from ``./qaops.yaml`` when
    present (relative to the current working directory). A developer who keeps a
    repo-root ``qaops.yaml`` for manual runs - e.g. ``provider: openrouter`` for
    live PDF testing - would otherwise have that file silently selected by every
    test that calls ``load_settings(None)`` (the API and CLI paths), so the suite
    would demand that provider's key and fail, even though nothing in the test
    chose that provider. The result would depend on the working directory the
    suite happens to run from.

    This isolates config discovery by chdir-ing each test into a clean temp
    directory that contains no ``qaops.yaml``. Tests do not rely on the working
    directory for file reads (they use ``tmp_path`` and explicit paths), so this
    is safe. It changes NOTHING about production ``load_settings`` behaviour - it
    only controls where the test process looks - and it never adds a config
    value, so it cannot mask a real misconfiguration.

    Tests that specifically exercise cwd-based ``qaops.yaml`` discovery opt out
    with ``@pytest.mark.uses_cwd_config`` and manage their own working directory.
    """
    if request.node.get_closest_marker("uses_cwd_config") is not None:
        yield
        return
    clean_cwd = tmp_path_factory.mktemp("no_ambient_config")
    monkeypatch.chdir(clean_cwd)
    yield


@pytest.fixture(autouse=True)
def _isolate_environment(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Remove ambient QAOPS_* and provider-key variables for each test.

    monkeypatch.delenv restores the original environment automatically at the
    end of the test, so a developer's real shell is untouched afterwards.

    Tests marked ``llm`` deliberately use real credentials (they are the opt-in
    live tests, guarded by their own skipif and excluded from CI via
    ``-m "not llm"``). Stripping keys from them would make a test that its own
    skipif decided to run then fail for lack of a key, so llm-marked tests keep
    the ambient provider keys. QAOPS_* settings are still normalised for them.
    """
    for name in list(os.environ):
        if name.startswith(_QAOPS_PREFIX):
            monkeypatch.delenv(name, raising=False)
    if request.node.get_closest_marker("llm") is None:
        for name in _PROVIDER_KEY_VARS:
            monkeypatch.delenv(name, raising=False)
    yield
