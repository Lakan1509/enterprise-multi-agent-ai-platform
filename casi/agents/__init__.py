"""Agent package: base contracts plus the concrete Milestone 1 agents.

The agents are deliberately decoupled from their sibling components
(``casi.memory``, ``casi.models``, ``casi.security``, ``casi.filesystem``,
``casi.execution``): they never import those packages at module top level.
``AgentContext`` carries them as ``typing.Any`` fields (or ``TYPE_CHECKING``
imports), so agents work with the real components and with lightweight
test fakes alike.
"""

from casi.agents.base import (
    Agent,
    AgentContext,
    AgentRegistry,
    AgentResult,
    NoCapableAgent,
)
from casi.agents.coder import CoderAgent
from casi.agents.debugger import DebuggerAgent
from casi.agents.planner_agent import PlannerAgent
from casi.agents.researcher import ResearcherAgent
from casi.agents.reviewer import ReviewerAgent
from casi.agents.supervisor import SupervisorAgent
from casi.agents.tester import TesterAgent

__all__ = [
    "Agent",
    "AgentContext",
    "AgentRegistry",
    "AgentResult",
    "NoCapableAgent",
    "CoderAgent",
    "DebuggerAgent",
    "PlannerAgent",
    "ResearcherAgent",
    "ReviewerAgent",
    "SupervisorAgent",
    "TesterAgent",
]
