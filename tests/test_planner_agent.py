"""Tests for the PlannerAgent (capability "plan")."""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

from casi.agents.planner_agent import PlannerAgent


class FakeWorking:
    def __init__(self, store):
        self._store = store

    def get(self, goal_id, key, default=None):
        return self._store.get((goal_id, key), default)

    def set(self, goal_id, key, value):
        self._store[(goal_id, key)] = value


class FakeMemory:
    def __init__(self, store):
        self.working = FakeWorking(store)


class FakeWorkspace:
    def __init__(self):
        self.files = {}

    def write(self, relpath, content, author=""):
        self.files[relpath] = content


def _ctx(findings):
    store = {("g1", "research:findings"): findings}
    task = SimpleNamespace(id="t2_plan_refine", params={"goal_hint": "build a thing"})
    return SimpleNamespace(
        goal_id="g1",
        task=task,
        workspace=FakeWorkspace(),
        memory=FakeMemory(store),
        audit=None,
    )


def test_planner_capability():
    assert "plan" in PlannerAgent.capabilities


def test_planner_builds_outline_from_findings():
    agent = PlannerAgent()
    ctx = _ctx(["finding one", "finding two"])
    result = agent.run(ctx)
    assert result.success
    assert "PLAN.md" in result.artifacts
    assert "finding one" in result.output["outline"]
    assert ctx.workspace.files["PLAN.md"].startswith("# Execution outline")


def test_planner_no_findings_still_succeeds():
    agent = PlannerAgent()
    ctx = _ctx([])
    result = agent.run(ctx)
    assert result.success
    assert "no prior findings" in result.output["outline"]
