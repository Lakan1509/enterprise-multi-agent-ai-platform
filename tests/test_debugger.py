"""Tests for casi.agents.debugger repair strategies."""

import json
import sys
from pathlib import Path
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


# --- end-to-end: real broken programs --------------------------------------
# Each test below runs a genuinely broken program under real pytest (via
# conftest.ExecSandbox, which truly executes commands without depending on
# casi.execution.sandbox — another stream's component, mid-rewrite), feeds
# the *real* failure output to the debugger, and then re-executes the
# patched program to prove the tests now pass. Nothing is canned.


class DirWorkspace:
    """Workspace fake backed by a real directory on disk."""

    def __init__(self, root):
        self.root = Path(root)
        self.writes: list[tuple] = []

    def write(self, relpath, content, author=""):
        (self.root / relpath).write_text(content, encoding="utf-8")
        self.writes.append((relpath, author))
        return relpath

    def read(self, relpath):
        return (self.root / relpath).read_text(encoding="utf-8")


def _real_pytest_report(sandbox, tmp_path, test_file):
    """Execute pytest for real in the sandbox; build a tester-style report."""
    from casi.agents.tester import _parse_counts

    result = sandbox.run(
        [sys.executable, "-m", "pytest", test_file, "-q"],
        cwd=str(tmp_path),
        timeout_s=120,
    )
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
    passed, failed = _parse_counts(combined)
    return {
        "test_file": test_file,
        "passed": passed,
        "failed": failed,
        "returncode": result.returncode,
        "timed_out": bool(result.timed_out),
        "stdout_tail": (result.stdout or "")[-2000:],
    }


def _debug_and_rerun(
    tmp_path, module_file, test_file, module_src, test_src, expected_strategy
):
    """Full repair loop against a real broken program; returns patched source."""
    from conftest import ExecSandbox

    ws = DirWorkspace(tmp_path)
    ws.write(module_file, module_src, author="test")
    ws.write(test_file, test_src, author="test")
    sandbox = ExecSandbox()

    report = _real_pytest_report(sandbox, tmp_path, test_file)
    assert report["failed"] > 0 and report["returncode"] != 0, (
        f"test program was not actually broken: {report}"
    )

    task = SimpleNamespace(
        id="t4",
        params={
            "target_file": module_file,
            "test_file": test_file,
            "test_report": report,
        },
    )
    ctx = AgentContext(
        goal_id="g1",
        task=task,
        workspace=ws,
        memory=FakeMemory(),
        models=None,
        sandbox=None,
        audit=FakeAudit(),
        approvals=None,
    )
    result = DebuggerAgent().run(ctx)
    assert result.success is True, f"debugger failed: {result.message}"
    assert result.output["strategy"] == expected_strategy

    patched = ws.read(module_file)
    rerun = _real_pytest_report(sandbox, tmp_path, test_file)
    assert rerun["failed"] == 0 and rerun["returncode"] == 0, (
        f"patched program still fails: {rerun}"
    )
    return patched


def test_e2e_repairs_inplace_sort_bug(tmp_path):
    patched = _debug_and_rerun(
        tmp_path,
        "sort_list.py",
        "test_sort_list.py",
        'def sort_list(lst):\n    """Sort ascending."""\n    return lst.sort()\n',
        "from sort_list import sort_list\n"
        "\n"
        "def test_empty():\n    assert sort_list([]) == []\n"
        "\n"
        "def test_reversed():\n    assert sort_list([3, 2, 1]) == [1, 2, 3]\n",
        "replace-inplace-sort-with-sorted",
    )
    assert "return sorted(lst)" in patched
    assert ".sort()" not in patched


def test_e2e_adds_missing_import(tmp_path):
    patched = _debug_and_rerun(
        tmp_path,
        "calc.py",
        "test_calc.py",
        "def root(x):\n    return math.sqrt(x)\n",
        "from calc import root\n"
        "\n"
        "def test_root():\n    assert root(16) == 4.0\n",
        "add-missing-import:math",
    )
    assert patched.startswith("import math\n")


def test_e2e_fixes_off_by_one_range(tmp_path):
    patched = _debug_and_rerun(
        tmp_path,
        "total.py",
        "test_total.py",
        "def total(lst):\n"
        "    s = 0\n"
        "    for i in range(len(lst)+1):\n"
        "        s += lst[i]\n"
        "    return s\n",
        "from total import total\n"
        "\n"
        "def test_sum():\n    assert total([1, 2, 3]) == 6\n"
        "\n"
        "def test_empty():\n    assert total([]) == 0\n",
        "fix-off-by-one-range-bound",
    )
    assert "range(len(lst))" in patched
    assert "+1" not in patched


def test_e2e_fixes_off_by_one_while(tmp_path):
    patched = _debug_and_rerun(
        tmp_path,
        "total2.py",
        "test_total2.py",
        "def total2(lst):\n"
        "    s = 0\n"
        "    i = 0\n"
        "    while i <= len(lst):\n"
        "        s += lst[i]\n"
        "        i += 1\n"
        "    return s\n",
        "from total2 import total2\n"
        "\n"
        "def test_sum():\n    assert total2([4, 5]) == 9\n",
        "fix-off-by-one-while-bound",
    )
    assert "while i < len(lst):" in patched


def test_e2e_fixes_wrong_return_type(tmp_path):
    patched = _debug_and_rerun(
        tmp_path,
        "adder.py",
        "test_adder.py",
        "def add(a, b):\n    return str(a + b)\n",
        "from adder import add\n"
        "\n"
        "def test_add():\n    assert add(2, 3) == 5\n"
        "\n"
        "def test_add_type():\n    assert isinstance(add(2, 3), int)\n",
        "remove-spurious-str-coercion",
    )
    assert "return a + b" in patched
    assert "str(" not in patched
