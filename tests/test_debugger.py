"""Tests for casi.agents.debugger repair strategies."""

import json
from types import SimpleNamespace

from casi.agents.base import AgentContext
from casi.agents.debugger import DebuggerAgent


# --- fakes ---------------------------------------------------------------


class FakeWorkspace:
    def __init__(self):
        self.files: dict[str, str] = {}
        self.writes: list[tuple] = []

    def write(self, relpath, content, author=""):
        self.files[relpath] = content
        self.writes.append((relpath, author))
        return relpath

    def read(self, relpath):
        return self.files[relpath]


class FakeMemory:
    def __init__(self):
        self.store: dict[str, object] = {}

    def set(self, key, value):
        self.store[key] = value

    def get(self, key, default=None):
        return self.store.get(key, default)


class FakeAudit:
    def __init__(self):
        self.events: list[tuple] = []

    def record(self, event, **kwargs):
        self.events.append((event, kwargs))
        return {"event": event}


def _ctx(task):
    return AgentContext(
        goal_id="g1",
        task=task,
        workspace=FakeWorkspace(),
        memory=FakeMemory(),
        models=None,
        sandbox=None,
        audit=FakeAudit(),
        approvals=None,
    )


BUGGY = 'def sort_list(lst):\n    """Sort."""\n    return lst.sort()\n'

FAILING_REPORT = {
    "test_file": "test_sort_list.py",
    "passed": 0,
    "failed": 5,
    "returncode": 1,
    "stdout_tail": (
        "FAILED test_sort_list.py::test_empty_list - assert None == []\n"
        "FAILED test_sort_list.py::test_reverse_sorted - assert None == [1, 2, 3]\n"
        "TypeError: 'NoneType' object is not iterable\n"
        "5 failed in 0.08s\n"
    ),
}


# --- strategy 1: in-place sort -------------------------------------------


def test_patches_inplace_sort_to_sorted():
    task = SimpleNamespace(
        id="t4",
        params={
            "target_file": "sort_list.py",
            "test_file": "test_sort_list.py",
            "test_report": FAILING_REPORT,
        },
    )
    ctx = _ctx(task)
    ctx.workspace.files["sort_list.py"] = BUGGY

    result = DebuggerAgent().run(ctx)

    assert result.success is True
    assert result.output["strategy"] == "replace-inplace-sort-with-sorted"
    assert result.artifacts == ["sort_list.py"]
    assert "replace-inplace-sort-with-sorted" in result.message

    patched = ctx.workspace.files["sort_list.py"]
    assert "return sorted(lst)" in patched
    assert ".sort()" not in patched

    # working-memory flags so coder/tests know a repair happened
    assert ctx.memory.store["repair:sort_list.py"] == "replace-inplace-sort-with-sorted"
    assert ctx.memory.store["repair:t4"] == "replace-inplace-sort-with-sorted"
    # audit recorded with the strategy name
    assert any(
        e[0] == "agent.debugger.patched"
        and e[1]["details"]["strategy"] == "replace-inplace-sort-with-sorted"
        for e in ctx.audit.events
    )


def test_report_loaded_from_workspace_path():
    report_path = "TEST_REPORT.json"
    task = SimpleNamespace(
        id="t4",
        params={"target_file": "sort_list.py", "test_report": report_path},
    )
    ctx = _ctx(task)
    ctx.workspace.files["sort_list.py"] = BUGGY
    ctx.workspace.files[report_path] = json.dumps(FAILING_REPORT)

    result = DebuggerAgent().run(ctx)

    assert result.success is True
    assert "return sorted(lst)" in ctx.workspace.files["sort_list.py"]


# --- strategy 2: missing stdlib import ------------------------------------


def test_adds_missing_stdlib_import():
    task = SimpleNamespace(
        id="t4",
        params={
            "target_file": "calc.py",
            "test_report": {
                "failed": 1,
                "stdout_tail": "NameError: name 'math' is not defined\n1 failed\n",
            },
        },
    )
    ctx = _ctx(task)
    ctx.workspace.files["calc.py"] = "def root(x):\n    return math.sqrt(x)\n"

    result = DebuggerAgent().run(ctx)

    assert result.success is True
    assert result.output["strategy"] == "add-missing-import:math"
    assert ctx.workspace.files["calc.py"].startswith("import math\n")


def test_name_error_for_non_allowlisted_name_has_no_strategy():
    task = SimpleNamespace(
        id="t4",
        params={
            "target_file": "app.py",
            "test_report": {
                "failed": 1,
                "stdout_tail": "NameError: name 'numpy' is not defined\n",
            },
        },
    )
    ctx = _ctx(task)
    ctx.workspace.files["app.py"] = "def f():\n    return numpy.zeros(3)\n"

    result = DebuggerAgent().run(ctx)

    assert result.success is False
    assert result.error == "no applicable repair strategy"
    assert ctx.memory.store["repair:requested"] == "app.py"


# --- fallback --------------------------------------------------------------


def test_no_strategy_returns_honest_failure():
    task = SimpleNamespace(
        id="t4",
        params={
            "target_file": "ok.py",
            "test_report": {
                "failed": 1,
                "stdout_tail": "ValueError: something unexpected\n1 failed\n",
            },
        },
    )
    ctx = _ctx(task)
    ctx.workspace.files["ok.py"] = "def f():\n    return 1\n"

    result = DebuggerAgent().run(ctx)

    assert result.success is False
    assert result.error == "no applicable repair strategy"
    assert ctx.memory.store["repair:requested"] == "ok.py"
    # source untouched
    assert ctx.workspace.files["ok.py"] == "def f():\n    return 1\n"


def test_missing_report_fails():
    task = SimpleNamespace(id="t4", params={"target_file": "ok.py"})
    ctx = _ctx(task)
    result = DebuggerAgent().run(ctx)
    assert result.success is False
    assert "test_report" in result.error
