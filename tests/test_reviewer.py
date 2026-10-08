"""Tests for casi.agents.reviewer (real compile checks + publish approvals)."""

from types import SimpleNamespace

from casi.agents.base import AgentContext
from casi.agents.reviewer import ReviewerAgent


# --- fakes ---------------------------------------------------------------


class FakeWorkspace:
    def __init__(self):
        self.files: dict[str, str] = {}

    def write(self, relpath, content, author=""):
        self.files[relpath] = content
        return relpath

    def read(self, relpath):
        return self.files[relpath]

    def exists(self, relpath):
        return relpath in self.files


class FakeApprovals:
    def __init__(self):
        self.requests: list[tuple] = []

    def request(self, action, details):
        self.requests.append((action, details))
        return SimpleNamespace(id="appr-1", action=action, status="pending")


def _ctx(task):
    return AgentContext(
        goal_id="g1",
        task=task,
        workspace=FakeWorkspace(),
        memory=None,
        models=None,
        sandbox=None,
        audit=None,
        approvals=FakeApprovals(),
    )


# --- review checks ---------------------------------------------------------


def test_valid_files_pass_review():
    task = SimpleNamespace(
        id="t6", params={"files": ["sort_list.py", "notes.txt"]}
    )
    ctx = _ctx(task)
    ctx.workspace.files["sort_list.py"] = "def sort_list(lst):\n    return sorted(lst)\n"
    ctx.workspace.files["notes.txt"] = "some notes"

    result = ReviewerAgent().run(ctx)

    assert result.success is True
    checks = result.output["checks"]
    assert checks["sort_list.py"]["passed"] is True
    assert checks["sort_list.py"]["compiles"] is True
    assert checks["notes.txt"]["passed"] is True
    assert checks["notes.txt"]["compiles"] is None  # not a python file


def test_syntax_error_file_fails_review():
    task = SimpleNamespace(id="t6", params={"files": ["bad.py"]})
    ctx = _ctx(task)
    ctx.workspace.files["bad.py"] = "def broken(:\n    pass\n"

    result = ReviewerAgent().run(ctx)

    assert result.success is False
    checks = result.output["checks"]
    assert checks["bad.py"]["compiles"] is False
    assert checks["bad.py"]["passed"] is False
    assert "SyntaxError" in checks["bad.py"]["error"]


def test_missing_and_empty_files_fail_review():
    task = SimpleNamespace(id="t6", params={"files": ["gone.py", "empty.py"]})
    ctx = _ctx(task)
    ctx.workspace.files["empty.py"] = "   \n"

    result = ReviewerAgent().run(ctx)

    assert result.success is False
    assert result.output["checks"]["gone.py"]["exists"] is False
    assert result.output["checks"]["empty.py"]["non_empty"] is False


def test_failing_test_report_fails_review():
    task = SimpleNamespace(
        id="t6",
        params={
            "files": ["sort_list.py"],
            "test_report": {"failed": 2, "passed": 1},
        },
    )
    ctx = _ctx(task)
    ctx.workspace.files["sort_list.py"] = "def sort_list(lst):\n    return sorted(lst)\n"

    result = ReviewerAgent().run(ctx)

    assert result.success is False
    assert result.output["checks"]["test_report"]["failed"] == 2


def test_clean_test_report_passes_review():
    task = SimpleNamespace(
        id="t6",
        params={
            "files": ["sort_list.py"],
            "test_report": {"failed": 0, "passed": 5},
        },
    )
    ctx = _ctx(task)
    ctx.workspace.files["sort_list.py"] = "def sort_list(lst):\n    return sorted(lst)\n"

    result = ReviewerAgent().run(ctx)

    assert result.success is True
    assert result.output["checks"]["test_report"]["passed"] is True


# --- publish flow ------------------------------------------------------------


def test_publish_action_requests_approval_without_deciding():
    task = SimpleNamespace(
        id="t7",
        params={"action": "publish", "files": ["sort_list.py"]},
        approval_required=True,
    )
    ctx = _ctx(task)

    result = ReviewerAgent().run(ctx)

    assert result.success is True
    assert result.output["approval_id"] == "appr-1"
    assert result.message == "publish awaiting approval"
    # approval requested exactly once with goal + files
    assert len(ctx.approvals.requests) == 1
    action, details = ctx.approvals.requests[0]
    assert action == "publish"
    assert details["goal_id"] == "g1"
    assert details["files"] == ["sort_list.py"]


def test_approval_required_task_triggers_publish_even_without_action():
    task = SimpleNamespace(
        id="t7", params={"files": ["sort_list.py"]}, approval_required=True
    )
    ctx = _ctx(task)

    result = ReviewerAgent().run(ctx)

    assert result.success is True
    assert result.output["approval_id"] == "appr-1"
