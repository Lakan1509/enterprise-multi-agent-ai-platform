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
    # with no findings the planner emits a clarify-first task, not garbage
    tasks = result.output["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["order"] == 1
    assert "Clarify" in tasks[0]["title"]
    assert "## Task list" in result.output["outline"]
    assert "1. Clarify the goal" in result.output["outline"]


def test_researcher_findings_flow_into_planner_agent():
    """End-to-end wiring: researcher writes findings, planner turns them into tasks."""
    from casi.agents.researcher import ResearcherAgent

    store: dict = {}

    class Ws:
        def __init__(self):
            self.files = {}

        def search(self, query, k=10):
            return [{"path": "notes/sort.md", "name": "sort.md"}]

        def write(self, relpath, content, author=""):
            self.files[relpath] = content

    class Mem:
        working = FakeWorking(store)
        longterm = SimpleNamespace(
            recall=lambda q, k=5: [{"text": "quicksort is O(n log n)", "id": "m1"}]
        )

    ws, mem = Ws(), Mem()
    research_ctx = SimpleNamespace(
        goal_id="g1",
        task=SimpleNamespace(id="t1", params={"query": "sorting"}),
        workspace=ws,
        memory=mem,
        models=None,
        sandbox=None,
        audit=None,
        approvals=None,
        role=None,
    )
    r_result = ResearcherAgent().run(research_ctx)
    assert r_result.success
    assert len(r_result.output["findings"]) == 2

    plan_ctx = SimpleNamespace(
        goal_id="g1",
        task=SimpleNamespace(id="t2", params={"goal_hint": "sort numbers"}),
        workspace=ws,
        memory=mem,
        models=None,
        sandbox=None,
        audit=None,
        approvals=None,
        role=None,
    )
    p_result = PlannerAgent().run(plan_ctx)
    assert p_result.success
    assert len(p_result.output["tasks"]) == 2
    details = " ".join(t["detail"] for t in p_result.output["tasks"])
    assert "sort.md" in details
    assert "quicksort" in details


def test_planner_emits_structured_task_list():
    agent = PlannerAgent()
    ctx = _ctx(["finding one", "finding two"])
    result = agent.run(ctx)
    tasks = result.output["tasks"]
    assert [t["order"] for t in tasks] == [1, 2]
    assert all(t["title"] and t["detail"] for t in tasks)
    assert tasks[0]["detail"] == "finding one"
    # task list persisted to working memory and to PLAN.md
    assert ctx.memory.working.get("g1", "plan:tasks") == tasks
    plan_md = ctx.workspace.files["PLAN.md"]
    assert "## Task list" in plan_md
    assert "1. Act on finding 1: finding one" in plan_md
    assert "2. Act on finding 2: finding two" in plan_md
