"""Tests for casi.agents.base (registry/contracts) and supervisor assignment."""

from types import SimpleNamespace

import pytest

from casi.agents.base import (
    Agent,
    AgentContext,
    AgentRegistry,
    AgentResult,
    NoCapableAgent,
)
from casi.agents.coder import CoderAgent
from casi.agents.supervisor import SupervisorAgent
from casi.agents.tester import TesterAgent


class _StubAgent(Agent):
    name = "stub"
    capabilities = ("stub-cap",)

    def run(self, ctx: AgentContext) -> AgentResult:
        return AgentResult(True, {}, [], "stub ok")


def _make_registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(CoderAgent())
    registry.register(TesterAgent())
    return registry


def _ctx(task: SimpleNamespace) -> AgentContext:
    return AgentContext(
        goal_id="g1",
        task=task,
        workspace=None,
        memory=None,
        models=None,
        sandbox=None,
        audit=None,
        approvals=None,
    )


# --- registry ----------------------------------------------------------


def test_register_find_list():
    registry = _make_registry()
    agents = registry.list()
    assert [a.name for a in agents] == ["coder", "tester"]
    assert registry.find("code").name == "coder"
    assert registry.find("test").name == "tester"


def test_register_duplicate_name_raises():
    registry = AgentRegistry()
    registry.register(_StubAgent())
    with pytest.raises(ValueError):
        registry.register(_StubAgent())


def test_find_unknown_capability_raises_no_capable_agent():
    registry = AgentRegistry()
    registry.register(_StubAgent())
    with pytest.raises(NoCapableAgent):
        registry.find("nonexistent-capability")


# --- supervisor --------------------------------------------------------


def test_supervisor_assign_returns_capable_agent():
    registry = _make_registry()
    supervisor = SupervisorAgent()
    task = SimpleNamespace(id="t1", agent_capability="code")
    agent = supervisor.assign(task, registry)
    assert agent.name == "coder"


def test_supervisor_assign_unknown_capability_clear_message():
    registry = AgentRegistry()
    supervisor = SupervisorAgent()
    task = SimpleNamespace(id="t9", agent_capability="fly")
    with pytest.raises(NoCapableAgent) as exc_info:
        supervisor.assign(task, registry)
    assert "fly" in str(exc_info.value)


def test_supervisor_run_reports_assigned_agent():
    registry = _make_registry()
    supervisor = SupervisorAgent(registry)
    task = SimpleNamespace(id="t2", agent_capability="test", params={})
    result = supervisor.run(_ctx(task))
    assert result.success is True
    assert result.output == {"assigned": "tester"}
    assert isinstance(result.message, str) and result.message


def test_supervisor_run_without_registry_fails_cleanly():
    supervisor = SupervisorAgent()
    task = SimpleNamespace(id="t3", agent_capability="code", params={})
    result = supervisor.run(_ctx(task))
    assert result.success is False
    assert result.error
