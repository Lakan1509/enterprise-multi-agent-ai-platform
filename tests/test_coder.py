"""Tests for casi.agents.coder with lightweight fake components."""

from types import SimpleNamespace

from casi.agents.base import AgentContext
from casi.agents.coder import CoderAgent


# --- fakes ---------------------------------------------------------------


class FakeWorkspace:
    """Captures writes; supports read for completeness."""

    def __init__(self):
        self.files: dict[str, str] = {}
        self.authors: dict[str, str] = {}

    def write(self, relpath, content, author=""):
        self.files[relpath] = content
        self.authors[relpath] = author
        return relpath

    def read(self, relpath):
        return self.files[relpath]


class FakeMemory:
    """Flat working-memory fake (dict-style get/set)."""

    def __init__(self):
        self.store: dict[str, object] = {}

    def set(self, key, value):
        self.store[key] = value

    def get(self, key, default=None):
        return self.store.get(key, default)


class FakeModels:
    """Fake model provider: returns canned text or raises."""

    def __init__(self, text=None, exc=None):
        self.text = text
        self.exc = exc
        self.calls: list[tuple] = []

    def complete(self, task_kind, system, user, **kw):
        self.calls.append((task_kind, system, user))
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(text=self.text)


class FakeAudit:
    def __init__(self):
        self.events: list[tuple] = []

    def record(self, event, **kwargs):
        self.events.append((event, kwargs))
        return {"event": event}


def _ctx(task, models=None):
    return AgentContext(
        goal_id="g1",
        task=task,
        workspace=FakeWorkspace(),
        memory=FakeMemory(),
        models=models if models is not None else FakeModels(exc=RuntimeError("no provider")),
        sandbox=None,
        audit=FakeAudit(),
        approvals=None,
    )


def _task(task_id="t1", **params):
    return SimpleNamespace(id=task_id, params=params)


# --- implementation ------------------------------------------------------


def test_implementation_first_pass_has_genuine_bug():
    ctx = _ctx(_task(kind="implementation", target_file="sort_list.py"))
    result = CoderAgent().run(ctx)

    assert result.success is True
    assert result.artifacts == ["sort_list.py"]
    assert result.output["file"] == "sort_list.py"

    code = ctx.workspace.files["sort_list.py"]
    assert "def sort_list(lst):" in code
    assert "return lst.sort()" in code  # the genuine bug: list.sort() -> None
    assert "sorted(" not in code

    assert ctx.memory.store["code:sort_list.py"] == "written"
    assert any(e[0] == "agent.coder.wrote" for e in ctx.audit.events)


def test_implementation_repair_mode_emits_correct_code():
    ctx = _ctx(
        _task(kind="implementation", target_file="sort_list.py", attempt="repair")
    )
    result = CoderAgent().run(ctx)

    assert result.success is True
    assert result.output["repair_mode"] is True
    code = ctx.workspace.files["sort_list.py"]
    assert "return sorted(lst)" in code
    assert ".sort()" not in code
    assert "TypeError" in code  # non-list input guard


def test_implementation_repair_via_memory_flag():
    ctx = _ctx(_task(kind="implementation", target_file="sort_list.py"))
    ctx.memory.set("repair:t1", "patched")
    result = CoderAgent().run(ctx)

    assert result.success is True
    assert result.output["repair_mode"] is True
    assert "return sorted(lst)" in ctx.workspace.files["sort_list.py"]


def test_implementation_missing_target_file_fails():
    ctx = _ctx(_task(kind="implementation"))
    result = CoderAgent().run(ctx)
    assert result.success is False
    assert result.error


def test_provider_text_is_used_when_usable():
    provider_code = "def sort_list(lst):\n    return [1]\n"
    ctx = _ctx(
        _task(kind="implementation", target_file="sort_list.py"),
        models=FakeModels(text=provider_code),
    )
    result = CoderAgent().run(ctx)
    assert result.success is True
    assert ctx.workspace.files["sort_list.py"] == provider_code
    # provider was actually consulted
    assert ctx.models.calls and ctx.models.calls[0][0] == "code"


def test_provider_garbage_falls_back_to_deterministic():
    ctx = _ctx(
        _task(kind="implementation", target_file="sort_list.py"),
        models=FakeModels(text="here is some prose without code"),
    )
    result = CoderAgent().run(ctx)
    assert result.success is True
    assert "return lst.sort()" in ctx.workspace.files["sort_list.py"]


# --- tests ---------------------------------------------------------------


def test_tests_kind_writes_pytest_file_with_real_tests():
    ctx = _ctx(
        _task(
            kind="tests",
            target_file="sort_list.py",
            test_file="test_sort_list.py",
        )
    )
    result = CoderAgent().run(ctx)

    assert result.success is True
    assert result.artifacts == ["test_sort_list.py"]
    code = ctx.workspace.files["test_sort_list.py"]

    test_fns = [line for line in code.splitlines() if line.startswith("def test_")]
    assert len(test_fns) >= 4, test_fns
    assert "from sort_list import sort_list" in code
    # spot-check real assertions (not trivially-true stubs)
    assert "== []" in code
    assert "isinstance(result, list)" in code


def test_tests_kind_defaults_function_name():
    ctx = _ctx(_task(kind="tests", test_file="test_sort_list.py"))
    result = CoderAgent().run(ctx)
    assert result.success is True
    assert "sort_list" in ctx.workspace.files["test_sort_list.py"]
