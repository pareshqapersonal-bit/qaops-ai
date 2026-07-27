"""Hard wall-clock deadline for one provider call (ADR-031).

Phase 16.2 passed `request_timeout_seconds` to the provider SDKs, but the
underlying httpx stack interprets a bare `timeout=N` as a PER-OPERATION timeout
(connect=N, read=N, write=N, pool=N) - not a total deadline. A server that
trickles a byte before each read-timeout window keeps resetting the read clock,
so the total call runs unbounded. That is exactly what a hanging free-tier
model did in the live acceptance test.

httpx has no total-deadline concept. The reliable way to bound total wall-clock
time AND actually terminate the network work is async cancellation: run the
request as a coroutine under `asyncio.wait_for`, whose cancellation propagates
through httpx's async transport and closes the socket (verified: no orphaned
connection remains). The per-operation timeout is kept as transport safety.

`run_with_deadline` is the one boundary where this happens. The synchronous
provider clients call it, so the CLI stays synchronous - one short-lived event
loop per call, no app-wide async migration (ADR-031, section 21).
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

# Small tolerance over the configured deadline for scheduling, exception
# propagation, and connection teardown (ADR-031, section 13). The deadline is
# "approximately N seconds", not exact.
DEADLINE_TOLERANCE_SECONDS = 0.5


class HardDeadlineExceeded(Exception):
    """A provider call exceeded its total wall-clock deadline and was cancelled.

    The message contains the word "timed out" so the existing policy classifies
    it as FailureKind.TIMEOUT without any special-casing (ADR-030).
    """

    def __init__(self, provider: str, deadline_seconds: float, elapsed_seconds: float) -> None:
        self.provider = provider
        self.deadline_seconds = deadline_seconds
        self.elapsed_seconds = elapsed_seconds
        super().__init__(
            f"request timed out after ~{elapsed_seconds:.1f}s "
            f"(hard deadline {deadline_seconds:g}s, provider={provider})"
        )


def run_with_deadline[T](
    coro_factory: Callable[[], Awaitable[T]],
    *,
    provider: str,
    deadline_seconds: float,
) -> T:
    """Run an async provider call under a hard total deadline, synchronously.

    `coro_factory` builds the awaitable inside the event loop (so the async HTTP
    client is created and closed in the same loop). If the total elapsed time
    exceeds `deadline_seconds`, the coroutine is cancelled - which closes the
    underlying connection - and HardDeadlineExceeded is raised. Uses a monotonic
    clock for the elapsed measurement (section 12).
    """
    started = time.monotonic()

    async def _runner() -> T:
        # wait_for cancels the wrapped coroutine on timeout; httpx's async
        # transport turns that cancellation into a closed socket.
        return await asyncio.wait_for(coro_factory(), timeout=deadline_seconds)

    try:
        return asyncio.run(_runner())
    except TimeoutError as exc:
        elapsed = time.monotonic() - started
        raise HardDeadlineExceeded(provider, deadline_seconds, elapsed) from exc
