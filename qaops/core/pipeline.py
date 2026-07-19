"""Sequential pipeline runner.

The runner is intentionally dumb: it executes stages in order, passes
each stage's typed output to the next stage's input, and wraps failures
in StageError with the failing stage's name. All intelligence lives in
the stages themselves.
"""

import logging
import time
from typing import Any

from pydantic import BaseModel

from qaops.core.errors import StageError
from qaops.core.protocols import PipelineStage

logger = logging.getLogger(__name__)


class Pipeline:
    """Runs an ordered sequence of PipelineStage instances.

    Type compatibility between adjacent stages is the composer's
    responsibility; the concrete Agent that builds a pipeline pins the
    exact stage types, so mismatches fail at construction in tests
    rather than in production.
    """

    def __init__(self, stages: list[PipelineStage[Any, Any]]) -> None:
        if not stages:
            msg = "Pipeline requires at least one stage"
            raise ValueError(msg)
        self._stages = list(stages)

    @property
    def stage_names(self) -> list[str]:
        return [stage.name for stage in self._stages]

    def run(self, data: BaseModel) -> BaseModel:
        """Execute all stages in order and return the final output."""
        current: BaseModel = data
        for stage in self._stages:
            started = time.perf_counter()
            logger.info("stage.start name=%s", stage.name)
            try:
                current = stage.run(current)
            except StageError:
                raise
            except Exception as exc:
                raise StageError(stage.name, str(exc)) from exc
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.info("stage.done name=%s elapsed_ms=%.1f", stage.name, elapsed_ms)
        return current
