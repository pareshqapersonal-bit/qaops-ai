"""QAOps agents (ADR-041).

The agent layer reasons about HOW the deterministic pipeline executes. It never
generates or mutates pipeline artifacts (requirements, business rules, gaps,
scenarios, conditions, test cases, coverage) - those remain owned by the
pipeline stages. This package is the extension point for future agents; today it
contains one, the OrchestratorAgent.
"""

from qaops.agent.agents import ExecutionAgent, PlanningAgent, ReflectionAgent
from qaops.agent.base import Agent
from qaops.agent.loop import GoalDrivenLoop, decide
from qaops.agent.models import (
    Decision,
    ExecutionPlan,
    LoopDecision,
    LoopIteration,
    LoopSummary,
    Observation,
    PlanStep,
    PlanStepStatus,
    Reflection,
    StageOutcome,
    TerminalReason,
)
from qaops.agent.observe import observe
from qaops.agent.orchestrator import OrchestratorAgent
from qaops.agent.planner import ExecutionPlanner
from qaops.agent.reflection import Reflector
from qaops.agent.supervisor import SupervisorAgent

__all__ = [
    "Agent",
    "Decision",
    "ExecutionAgent",
    "ExecutionPlan",
    "ExecutionPlanner",
    "GoalDrivenLoop",
    "LoopDecision",
    "LoopIteration",
    "LoopSummary",
    "Observation",
    "OrchestratorAgent",
    "PlanStep",
    "PlanStepStatus",
    "PlanningAgent",
    "Reflection",
    "ReflectionAgent",
    "Reflector",
    "StageOutcome",
    "SupervisorAgent",
    "TerminalReason",
    "decide",
    "observe",
]
