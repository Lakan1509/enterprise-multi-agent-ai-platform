"""Base contracts for CASI agents.

Defines the agent interface (``Agent``), the result/context dataclasses
(``AgentResult``, ``AgentContext``), the capability-based registry
(``AgentRegistry``), and ``NoCapableAgent``.

Decoupling note: this module does not import any sibling CASI component
(workspace, memory, models, sandbox, audit, approvals) at module top level.
``AgentContext`` declares those fields as ``typing.Any`` so agents can run
against the real components or lightweight test fakes. ``TaskSpec`` is
referenced only under ``TYPE_CHECKING`` because ``casi.planner`` may not
exist yet. The one sanctioned cross-package call is
:func:`require_capability`, which imports ``casi.security.permissions``
*lazily* (inside the function) so import time stays decoupled.
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
    role: Any = None  # casi.security.permissions.Role of the principal
    # running this agent. ``None`` means "no role wired" (legacy contexts,
    # e.g. built before role wiring existed): such contexts are NOT gated by
    # require_capability. Any explicit role — including unknown ones — fails
    # closed via PermissionDenied.


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


def require_capability(ctx: AgentContext, capability: Any) -> None:
    """Enforce the role recorded on ``ctx`` holds ``capability``.

    This is the single choke point through which every agent's
    side-effecting action (workspace writes, code execution, workspace
    reads/searches) must pass. It delegates to
    :func:`casi.security.permissions.check`, imported lazily so this module
    keeps no import-time coupling to ``casi.security``.

    Args:
        ctx: The agent context carrying the principal's ``role``.
        capability: A ``casi.security.permissions.Capability`` member or its
            string name (e.g. ``"EXECUTE_CODE"``).

    Behavior:
        - ``ctx.role is None`` (legacy context, role never wired): not gated,
          returns silently. Production wiring must set roles (see kernel).
        - Any explicit role: fails closed — :class:`PermissionDenied` is
          raised when the role lacks the capability or is unknown.

    Raises:
        PermissionDenied: If the explicit role lacks the capability.
    """
    role = getattr(ctx, "role", None)
    if role is None:
        return
    from casi.security.permissions import Capability, check

    check(role, Capability(capability))
