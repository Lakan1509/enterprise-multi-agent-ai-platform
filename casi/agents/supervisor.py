"""Supervisor agent: assigns tasks to agents by capability.

The supervisor does no work itself; it resolves ``task.agent_capability``
against an ``AgentRegistry`` and reports which agent was chosen. The kernel
(or scheduler) then runs the chosen agent.
"""

from __future__ import annotations

from typing import Any

from casi.agents.base import Agent, AgentContext, AgentRegistry, AgentResult, NoCapableAgent


class SupervisorAgent(Agent):
    """Routes tasks to capable agents.

    Args:
        registry: The agent registry used by :meth:`run`. :meth:`assign`
            can also be called with an explicit registry.
    """

    name = "supervisor"
    capabilities = ("supervise",)

    def __init__(self, registry: AgentRegistry | None = None) -> None:
        self._registry = registry

    def assign(self, task: Any, registry: AgentRegistry) -> Agent:
        """Return the agent that can handle ``task.agent_capability``.

        Raises:
            NoCapableAgent: With a clear message naming the missing
                capability and the task, when no agent matches.
        """
        capability = getattr(task, "agent_capability", None)
        task_id = getattr(task, "id", "?")
        try:
            return registry.find(capability)
        except NoCapableAgent as exc:
            raise NoCapableAgent(
                f"supervisor: no agent registered for capability {capability!r} "
                f"(task {task_id!r})"
            ) from exc

    def run(self, ctx: AgentContext) -> AgentResult:
        """Assign ``ctx.task`` and report the chosen agent."""
        if self._registry is None:
            return AgentResult(
                success=False,
                output={},
                artifacts=[],
                message="supervisor has no registry configured",
                error="supervisor registry is None",
            )
        agent = self.assign(ctx.task, self._registry)
        task_id = getattr(ctx.task, "id", "?")
        return AgentResult(
            success=True,
            output={"assigned": agent.name},
            artifacts=[],
            message=f"assigned task {task_id!r} to agent {agent.name!r}",
        )
