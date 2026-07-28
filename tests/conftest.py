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
