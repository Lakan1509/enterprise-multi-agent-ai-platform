"""Permission enforcement on agent side-effecting actions.

Every agent's side-effecting action goes through
``casi.agents.base.require_capability`` at the point of action, delegating to
``casi.security.permissions.check``. A VIEWER-role agent attempting a gated
action fails closed with ``PermissionDenied`` *before* the side effect
happens; roles that hold the capability proceed; legacy contexts with no
role wired (``role=None``) are not gated.
"""

from types import SimpleNamespace

import pytest

from casi.agents.base import AgentContext, require_capability
from casi.agents.coder import CoderAgent
from casi.agents.debugger import DebuggerAgent
from casi.agents.planner_agent import PlannerAgent
from casi.agents.researcher import ResearcherAgent
from casi.agents.reviewer import ReviewerAgent
from casi.agents.tester import TesterAgent, REPORT_FILENAME
from casi.security.permissions import Capability, PermissionDenied, Role


# --- fakes ---------------------------------------------------------------


class FakeWorkspace:
    def __init__(self):
        self.files: dict[str, str] = {}
        self.writes: list[str] = []
        self.reads: list[str] = []
        self.searches: list[str] = []

    def write(self, relpath, content, author=""):
        self.writes.append(relpath)
        self.files[relpath] = content
        return relpath

    def read(self, relpath):
        self.reads.append(relpath)
        return self.files[relpath]

    def exists(self, relpath):
        return relpath in self.files

    def search(self, query, k=10):
        self.searches.append(query)
        return []


class FakeSandbox:
    def __init__(self):
        self.calls: list = []

    def run(self, cmd, cwd=None, timeout_s=60, env=None):
        self.calls.append(cmd)
        return SimpleNamespace(
            returncode=0, stdout="1 passed", stderr="", timed_out=False
        )


class FakeMemory:
    def __init__(self):
        self.store: dict = {}

    def set(self, key, value):
        self.store[key] = value

    def get(self, key, default=None):
        return self.store.get(key, default)


def _ctx(task, role, workspace=None, sandbox=None, memory=None):
    return AgentContext(
        goal_id="g1",
        task=task,
        workspace=workspace if workspace is not None else FakeWorkspace(),
        memory=memory if memory is not None else FakeMemory(),
        models=None,
        sandbox=sandbox,
        audit=None,
        approvals=None,
        role=role,
    )


def _task(**params):
    return SimpleNamespace(id="t1", params=params)


# --- require_capability unit behavior ------------------------------------


def test_viewer_lacks_execute_code():
    ctx = _ctx(_task(), Role.VIEWER)
    with pytest.raises(PermissionDenied):
        require_capability(ctx, "EXECUTE_CODE")


def test_viewer_may_read_workspace():
    ctx = _ctx(_task(), Role.VIEWER)
    require_capability(ctx, Capability.READ_WORKSPACE)  # must not raise


def test_unknown_role_fails_closed():
    ctx = _ctx(_task(), "NOT_A_ROLE")
    with pytest.raises(PermissionDenied):
        require_capability(ctx, "READ_WORKSPACE")


def test_legacy_context_without_role_is_not_gated():
    ctx = _ctx(_task(), None)
    require_capability(ctx, "EXECUTE_CODE")  # must not raise


# --- per-agent enforcement ------------------------------------------------


def test_viewer_tester_cannot_execute_code():
    sandbox = FakeSandbox()
    ctx = _ctx(
        _task(test_file="test_x.py"), Role.VIEWER, sandbox=sandbox
    )
    with pytest.raises(PermissionDenied):
        TesterAgent().run(ctx)
    assert sandbox.calls == []  # denied BEFORE the sandbox ran
    assert REPORT_FILENAME not in ctx.workspace.files


def test_operator_tester_can_execute_and_write_report():
    sandbox = FakeSandbox()
    ctx = _ctx(_task(test_file="test_x.py"), Role.OPERATOR, sandbox=sandbox)
    result = TesterAgent().run(ctx)
    assert result.success is True
    assert sandbox.calls != []
    assert REPORT_FILENAME in ctx.workspace.files


def test_viewer_coder_cannot_write():
    ctx = _ctx(
        _task(kind="implementation", target_file="out.py"), Role.VIEWER
    )
    with pytest.raises(PermissionDenied):
        CoderAgent().run(ctx)
    assert ctx.workspace.writes == []


def test_operator_coder_can_write():
    ctx = _ctx(
        _task(kind="implementation", target_file="out.py"), Role.OPERATOR
    )
    result = CoderAgent().run(ctx)
    assert result.success is True
    assert ctx.workspace.writes == ["out.py"]


def test_viewer_debugger_may_read_but_not_patch():
    ws = FakeWorkspace()
    ws.files["bug.py"] = "def f(lst):\n    return lst.sort()\n"
    report = {
        "failed": 1,
        "stdout_tail": "FAILED t - assert None == []\nTypeError: 'NoneType'\n",
    }
    ctx = _ctx(
        _task(target_file="bug.py", test_file="t.py", test_report=report),
        Role.VIEWER,
        workspace=ws,
    )
    with pytest.raises(PermissionDenied):
        DebuggerAgent().run(ctx)
    # the read happened (READ_WORKSPACE is allowed for VIEWER) but the source
    # was NOT patched — denial fired before the write.
    assert ws.reads == ["bug.py"]
    assert ws.writes == []
    assert ".sort()" in ws.files["bug.py"]


def test_operator_debugger_can_patch():
    ws = FakeWorkspace()
    ws.files["bug.py"] = "def f(lst):\n    return lst.sort()\n"
    report = {
        "failed": 1,
        "stdout_tail": "FAILED t - assert None == []\nTypeError: 'NoneType'\n",
    }
    ctx = _ctx(
        _task(target_file="bug.py", test_file="t.py", test_report=report),
        Role.OPERATOR,
        workspace=ws,
    )
    result = DebuggerAgent().run(ctx)
    assert result.success is True
    assert "sorted(" in ws.files["bug.py"]


def test_viewer_reviewer_may_read_files():
    ws = FakeWorkspace()
    ws.files["ok.py"] = "def f():\n    return 1\n"
    ctx = _ctx(_task(files=["ok.py"]), Role.VIEWER, workspace=ws)
    result = ReviewerAgent().run(ctx)
    assert result.success is True


def test_viewer_researcher_may_search():
    ctx = _ctx(_task(query="sorting"), Role.VIEWER)
    result = ResearcherAgent().run(ctx)
    assert result.success is True
    assert ctx.workspace.searches == ["sorting"]


def test_viewer_planner_agent_cannot_write_plan():
    mem = FakeMemory()
    ctx = _ctx(_task(goal_hint="x"), Role.VIEWER, memory=mem)
    with pytest.raises(PermissionDenied):
        PlannerAgent().run(ctx)
    assert "PLAN.md" not in ctx.workspace.files


def test_legacy_ungated_context_still_runs_agents():
    # contexts built without a role (e.g. by older wiring) keep working —
    # enforcement only bites once a role is actually carried.
    sandbox = FakeSandbox()
    ctx = _ctx(_task(test_file="test_x.py"), None, sandbox=sandbox)
    assert TesterAgent().run(ctx).success is True
    ctx2 = _ctx(_task(kind="implementation", target_file="o.py"), None)
    assert CoderAgent().run(ctx2).success is True
