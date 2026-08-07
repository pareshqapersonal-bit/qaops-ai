"""Deterministic quality review (ADR-045, Phase 30).

A read-only, advisory layer that runs AFTER the pipeline completes. The
QualityReviewer consumes a finished TestDesignResult - in particular its already
-computed CoverageReport - and produces a ReviewReport of objective findings. It
is not an Agent, makes no LLM calls, mutates nothing, generates no artifact,
invokes no stage, writes no checkpoint, and never feeds back into generation.
"""

from qaops.review.reviewer import QualityReviewer

__all__ = ["QualityReviewer"]
