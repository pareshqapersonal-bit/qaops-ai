"""The seam that makes every provider call visible to execution (ADR-030).

`generate_structured` runs a repair loop that can call the provider more than
once per stage invocation. Before this seam existed those calls were invisible
to the AdaptiveExecutor's budget and progress, so one executor "attempt" could
hide three real provider calls.

A RequestObserver is passed into `generate_structured` (directly, or via the
ambient context variable the executor binds around each stage run). It is
notified before every real `client.complete`, and its `before_request` may raise
`RequestBudgetExhausted` to stop further calls when the execution layer's budget
is spent. This keeps the invariant: one actual provider call == one counted
request, and the execution layer - not the structured-output layer - owns when
to stop.

The default observer (`NullRequestObserver`) does nothing, so `generate_structured`
remains usable outside the executor (tests, scripts) with no wiring.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol


class RequestBudgetExhausted(Exception):
    """Raised by an observer to stop the structured-output repair loop.

    The structured layer catches nothing here - it propagates so the caller
    (the executor, via the stage) sees that the provider-call budget stopped the
    work, distinct from a schema failure.
    """


class RequestObserver(Protocol):
    """Notified around each real provider generation call."""

    def before_request(self, *, provider: str, model: str, attempt: int) -> None:
        """Called immediately before a provider call.

        `attempt` is 1-based within the current structured-output invocation.
        May raise RequestBudgetExhausted to forbid the call.
        """
        ...

    def after_request(
        self, *, provider: str, model: str, attempt: int, empty: bool, chars: int
    ) -> None:
        """Called immediately after a provider call returns (no exception)."""
        ...


class NullRequestObserver:
    """An observer that counts nothing and never vetoes."""

    def before_request(self, *, provider: str, model: str, attempt: int) -> None:
        return None

    def after_request(
        self, *, provider: str, model: str, attempt: int, empty: bool, chars: int
    ) -> None:
        return None


# --- ambient observer -------------------------------------------------------
# The executor runs a stage via `stage.run()`, several call layers above
# `generate_structured`, and stages take their client at construction. Threading
# an observer through every stage constructor and the PipelineStage protocol
# would be a broad change for one cross-cutting concern. Instead the executor
# binds the active observer in a context variable around each stage run, and the
# structured-output helper reads it. The scope is one synchronous stage call, so
# there is no cross-run leakage.
#
# The ContextVar default is None (not a mutable instance); `current_observer`
# substitutes a fresh NullRequestObserver, which is stateless anyway.
_active_observer: ContextVar[RequestObserver | None] = ContextVar(
    "qaops_request_observer", default=None
)


def current_observer() -> RequestObserver:
    """The observer bound for the current stage run, or a null one."""
    return _active_observer.get() or NullRequestObserver()


@contextmanager
def observing(observer: RequestObserver) -> Iterator[None]:
    """Bind `observer` as the active request observer for the duration."""
    token = _active_observer.set(observer)
    try:
        yield
    finally:
        _active_observer.reset(token)
