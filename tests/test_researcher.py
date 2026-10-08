"""Tests for casi.agents.researcher (workspace search + memory recall)."""

from types import SimpleNamespace

from casi.agents.base import AgentContext
from casi.agents.researcher import ResearcherAgent


# --- fakes ---------------------------------------------------------------


class FakeWorkspace:
    def __init__(self, hits):
        self._hits = hits
        self.queries: list[str] = []

    def search(self, query, k=10):
        self.queries.append(query)
        return self._hits


class FakeMemory:
    def __init__(self, recalls):
        self.store: dict[str, object] = {}
        self.recall_queries: list[str] = []

        def _recall(query, k=5):
            self.recall_queries.append(query)
            return recalls

        self.longterm = SimpleNamespace(recall=_recall)

    def set(self, key, value):
        self.store[key] = value

    def get(self, key, default=None):
        return self.store.get(key, default)


class BareMemory:
    """Memory fake with no longterm attribute at all."""

    def __init__(self):
        self.store: dict[str, object] = {}

    def set(self, key, value):
        self.store[key] = value

    def get(self, key, default=None):
        return self.store.get(key, default)


def _ctx(task, workspace, memory):
    return AgentContext(
        goal_id="g1",
        task=task,
        workspace=workspace,
        memory=memory,
        models=None,
        sandbox=None,
        audit=None,
        approvals=None,
    )


# --- tests ---------------------------------------------------------------


def test_aggregates_workspace_and_memory_findings():
    ws_hits = [{"path": "notes/sort.md", "name": "sort.md"}]
    mem_hits = [{"text": "sorting is O(n log n)", "id": "m1"}]
    task = SimpleNamespace(id="t0", params={"query": "sorting"})
    ctx = _ctx(task, FakeWorkspace(ws_hits), FakeMemory(mem_hits))

    result = ResearcherAgent().run(ctx)

    assert result.success is True
    findings = result.output["findings"]
    assert len(findings) == 2
    sources = {f["source"] for f in findings}
    assert sources == {"workspace", "memory"}
    ws_finding = next(f for f in findings if f["source"] == "workspace")
    assert ws_finding["path"] == "notes/sort.md"
    mem_finding = next(f for f in findings if f["source"] == "memory")
    assert mem_finding["text"] == "sorting is O(n log n)"

    # findings stored in working memory
    stored = ctx.memory.store["research:t0"]
    assert stored == findings


def test_workspace_without_search_is_tolerated():
    task = SimpleNamespace(id="t0", params={"query": "x"})
    ctx = _ctx(task, object(), FakeMemory([{"text": "a fact"}]))

    result = ResearcherAgent().run(ctx)

    assert result.success is True
    assert len(result.output["findings"]) == 1
    assert result.output["findings"][0]["source"] == "memory"


def test_memory_without_longterm_is_tolerated():
    task = SimpleNamespace(id="t0", params={"query": "x"})
    ctx = _ctx(task, FakeWorkspace([{"path": "a.md", "name": "a.md"}]), BareMemory())

    result = ResearcherAgent().run(ctx)

    assert result.success is True
    assert len(result.output["findings"]) == 1
    assert result.output["findings"][0]["source"] == "workspace"
    assert ctx.memory.store["research:t0"] == result.output["findings"]


def test_query_is_forwarded_to_both_sources():
    """Findings are grounded: the task's query string is what gets searched
    and recalled — the researcher never invents findings."""
    ws = FakeWorkspace([{"path": "a.md", "name": "a.md"}])
    mem = FakeMemory([{"text": "a fact"}])
    task = SimpleNamespace(id="t0", params={"query": "sorting algorithms"})
    ctx = _ctx(task, ws, mem)

    ResearcherAgent().run(ctx)

    assert ws.queries == ["sorting algorithms"]
    assert mem.recall_queries == ["sorting algorithms"]


def test_empty_results_still_succeed():
    task = SimpleNamespace(id="t0", params={"query": "nothing-matches"})
    ctx = _ctx(task, FakeWorkspace([]), FakeMemory([]))

    result = ResearcherAgent().run(ctx)

    assert result.success is True
    assert result.output["findings"] == []
