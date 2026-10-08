"""Tests for casi.agents.tester with a fake sandbox."""

import json
from types import SimpleNamespace

from casi.agents.base import AgentContext
from casi.agents.tester import TesterAgent, REPORT_FILENAME


# --- fakes ---------------------------------------------------------------


class FakeSandbox:
    """Returns canned ExecResult-like objects; records the last call."""

    def __init__(self, returncode=0, stdout="", stderr="", timed_out=False):
        self._result = SimpleNamespace(
            returncode=returncode, stdout=stdout, stderr=stderr, timed_out=timed_out
        )
        self.last_call: dict = {}

    def run(self, cmd, cwd=None, timeout_s=60, env=None):
        self.last_call = {"cmd": cmd, "cwd": cwd, "timeout_s": timeout_s, "env": env}
        return self._result


class FakeWorkspace:
    def __init__(self, root="/fake/work"):
        self.files: dict[str, str] = {}
        self.root = root

    def write(self, relpath, content, author=""):
        self.files[relpath] = content
        return relpath

    def read(self, relpath):
        return self.files[relpath]


def _ctx(task, sandbox):
    return AgentContext(
        goal_id="g1",
        task=task,
        workspace=FakeWorkspace(),
        memory=None,
        models=None,
        sandbox=sandbox,
        audit=None,
        approvals=None,
    )


# --- tests ---------------------------------------------------------------


def test_passing_run_reports_success_and_counts():
    sandbox = FakeSandbox(returncode=0, stdout="3 passed in 0.04s\n")
    task = SimpleNamespace(id="t3", params={"test_file": "test_sort_list.py"})
    ctx = _ctx(task, sandbox)

    result = TesterAgent().run(ctx)

    assert result.success is True
    assert result.output["passed"] == 3
    assert result.output["failed"] == 0
    assert result.artifacts == ["test_sort_list.py", REPORT_FILENAME]

    # pytest was invoked correctly through the sandbox
    cmd = sandbox.last_call["cmd"]
    assert cmd[0].endswith("python") or "python" in cmd[0]
    assert cmd[1:4] == ["-m", "pytest", "test_sort_list.py"]
    assert "-q" in cmd

    # TEST_REPORT.json was written to the workspace
    report = json.loads(ctx.workspace.files[REPORT_FILENAME])
    assert report["test_file"] == "test_sort_list.py"
    assert report["passed"] == 3
    assert report["failed"] == 0
    assert report["returncode"] == 0
    assert "stdout_tail" in report


def test_failing_run_reports_failure_and_counts():
    sandbox = FakeSandbox(
        returncode=1,
        stdout="FAILED test_x.py::test_a - assert...\n2 failed, 1 passed in 0.10s\n",
        stderr="",
    )
    task = SimpleNamespace(
        id="t3", params={"test_file": "test_x.py", "timeout_s": 30}
    )
    ctx = _ctx(task, sandbox)

    result = TesterAgent().run(ctx)

    assert result.success is False
    assert result.output["passed"] == 1
    assert result.output["failed"] == 2
    assert result.error is not None
    assert sandbox.last_call["timeout_s"] == 30

    report = json.loads(ctx.workspace.files[REPORT_FILENAME])
    assert report["failed"] == 2
    assert report["returncode"] == 1


def test_missing_test_file_param_fails():
    sandbox = FakeSandbox()
    task = SimpleNamespace(id="t3", params={})
    result = TesterAgent().run(_ctx(task, sandbox))
    assert result.success is False
    assert result.error == "missing test_file"
