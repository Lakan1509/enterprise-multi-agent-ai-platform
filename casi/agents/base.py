"""Base contracts for CASI agents.

Defines the agent interface (``Agent``), the result/context dataclasses
(``AgentResult``, ``AgentContext``), the capability-based registry
(``AgentRegistry``), and ``NoCapableAgent``.

Decoupling note: this module does not import any sibling CASI component
(workspace, memory, models, sandbox, audit, approvals). ``AgentContext``
declares those fields as ``typing.Any`` so agents can run against the real
components or lightweight test fakes. ``TaskSpec`` is referenced only under
``TYPE_CHECKING`` because ``casi.planner`` may not exist yet.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from casi.planner import TaskSpec


class NoCapableAgent(Exception):
    """Raised when no registered agent can handle a requested capability."""


@dataclass
class AgentResult:
    """Outcome of a single agent run.

    Attributes:
        success: Whether the agent accomplished its task.
        output: Structured result payload (agent-specific keys).
        artifacts: Workspace-relative paths the agent produced or touched.
        message: Short human-readable summary.
        error: Machine-readable failure reason; None on success.
    """

    success: bool
    output: dict[str, Any]
    artifacts: list[str]
    message: str
    error: str | None = None


@dataclass
class AgentContext:
    """Everything an agent needs to do its work, wired by the kernel.

    Component fields are typed as ``Any`` on purpose: agents must not depend
    on the concrete sibling components at import time. Real usage wires the
    actual ``Workspace``, ``MemorySystem``, ``ModelRouter``, ``Sandbox``,
    ``AuditLog`` and ``ApprovalGate``; tests wire fakes.
    """

    goal_id: str
    task: Any  # TaskSpec (casi.planner) — Any until that module exists
    workspace: Any  # casi.filesystem.workspace.Workspace
    memory: Any  # casi.memory.store.MemorySystem
    models: Any  # casi.models.providers.ModelRouter
    sandbox: Any  # casi.execution.sandbox.Sandbox
    audit: Any  # casi.security.audit.AuditLog
    approvals: Any  # casi.security.approvals.ApprovalGate


class Agent(abc.ABC):
    """Abstract base class for all CASI agents."""

    name: str
    capabilities: tuple[str, ...]

    @abc.abstractmethod
    def run(self, ctx: AgentContext) -> AgentResult:
        """Execute the agent's work for ``ctx.task`` and return a result."""
        raise NotImplementedError


class AgentRegistry:
    """Maps agent names to agents and resolves agents by capability."""

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        """Register an agent.

        Raises:
            ValueError: If an agent with the same name is already registered.
        """
        if agent.name in self._agents:
            raise ValueError(f"agent already registered: {agent.name!r}")
        self._agents[agent.name] = agent

    def find(self, capability: str) -> Agent:
        """Return the first registered agent advertising ``capability``.

        Raises:
            NoCapableAgent: If no registered agent has the capability.
        """
        for agent in self._agents.values():
            if capability in agent.capabilities:
                return agent
        raise NoCapableAgent(f"no agent registered with capability {capability!r}")

    def list(self) -> list[Agent]:
        """Return all registered agents in registration order."""
        return list(self._agents.values())
