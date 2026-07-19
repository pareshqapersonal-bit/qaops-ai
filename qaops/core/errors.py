"""Exception hierarchy for QAOps.

All QAOps errors derive from QAOpsError so callers can catch one type
at the boundary. Stages wrap their failures in StageError with the
stage name attached, which keeps pipeline error messages actionable.
"""


class QAOpsError(Exception):
    """Base class for all QAOps errors."""


class ConfigurationError(QAOpsError):
    """Invalid or missing configuration."""


class StageError(QAOpsError):
    """A pipeline stage failed to produce a valid output."""

    def __init__(self, stage_name: str, message: str) -> None:
        self.stage_name = stage_name
        super().__init__(f"[{stage_name}] {message}")


class LLMError(QAOpsError):
    """The LLM provider failed or returned unusable output after retries."""


class InputTooLargeError(QAOpsError):
    """The requirement input exceeds the configured token/size budget."""


class ExportError(QAOpsError):
    """An exporter failed to write its output."""
