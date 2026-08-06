"""Specialized agents coordinated by the SupervisorAgent (ADR-043, Phase 28).

Phase 28 decomposes the monolithic OrchestratorAgent into three narrow agents -
planning, execution, reflection - each owning one responsibility and each an
Agent (base.py). This is a pure structural refactor: the agents wrap the exact
collaborators the orchestrator already used (ExecutionPlanner, DesignService
delegation, Reflector), so behaviour is byte-identical to Phase 27.
"""

from qaops.agent.agents.execution import ExecutionAgent
from qaops.agent.agents.planning import PlanningAgent
from qaops.agent.agents.reflection import ReflectionAgent

__all__ = ["ExecutionAgent", "PlanningAgent", "ReflectionAgent"]
