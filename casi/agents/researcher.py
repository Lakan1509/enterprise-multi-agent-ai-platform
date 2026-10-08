"""Researcher agent: gathers context from workspace files and long-term memory.

No web access — this agent only consults ``ctx.workspace.search(query)``
and ``ctx.memory.longterm.recall(query)``, both guarded so the agent still
works when a fake component lacks one of them. Findings are stored in
working memory under ``f"research:{task.id}"`` and aggregated under
``"research:findings"`` (the key the planner agent reads), and returned in
``output["findings"]``.

Permission gating: the workspace search goes through
:func:`casi.agents.base.require_capability` (``READ_WORKSPACE``) and fails
closed with ``PermissionDenied`` when the context carries a role that lacks
it.
"""

from __future__ import annotations

from typing import Any

from casi.agents.base import Agent, AgentContext, AgentResult, require_capability


def _task_params(task: Any) -> dict:
    params = getattr(task, "params", None)
    return dict(params) if isinstance(params, dict) else {}


def _task_id(task: Any) -> str:
    return str(getattr(task, "id", "unknown"))


def _item_field(item: Any, *names: str) -> Any:
    """Read a field from a FileMeta-like object or a plain dict."""
    if isinstance(item, dict):
        for name in names:
            if name in item:
                return item[name]
        return None
    for name in names:
        value = getattr(item, name, None)
        if value is not None:
            return value
    return None


def _mem_get(ctx: AgentContext, key: str, default: Any = None) -> Any:
    """Best-effort working-memory read. Never raises."""
    mem = getattr(ctx, "memory", None)
    if mem is None:
        return default
    working = getattr(mem, "working", None)
    getter = getattr(working, "get", None) if working is not None else None
    if callable(getter):
        try:
            return getter(ctx.goal_id, key, default)
        except Exception:
            pass
    getter = getattr(mem, "get", None)
    if callable(getter):
        try:
            return getter(ctx.goal_id, key, default)
        except TypeError:
            try:
                return getter(key, default)
            except Exception:
                pass
        except Exception:
            pass
    return default


def _mem_set(ctx: AgentContext, key: str, value: Any) -> None:
    """Best-effort working-memory write. Never raises."""
    mem = getattr(ctx, "memory", None)
    if mem is None:
        return
    working = getattr(mem, "working", None)
    setter = getattr(working, "set", None) if working is not None else None
    if callable(setter):
        try:
            setter(ctx.goal_id, key, value)
            return
        except Exception:
            pass
    setter = getattr(mem, "set", None)
    if callable(setter):
        try:
            setter(ctx.goal_id, key, value)
            return
        except TypeError:
            try:
                setter(key, value)
            except Exception:
                pass
        except Exception:
            pass


class ResearcherAgent(Agent):
    """Collects findings from workspace search and long-term memory recall."""

    name = "researcher"
    capabilities = ("research",)

    def run(self, ctx: AgentContext) -> AgentResult:
        """Search workspace + memory for ``params["query"]`` and store findings."""
        params = _task_params(ctx.task)
        query = params.get("query", "")

        findings: list[dict[str, Any]] = []

        search = getattr(ctx.workspace, "search", None)
        if callable(search):
            require_capability(ctx, "READ_WORKSPACE")
            try:
                for item in search(query) or []:
                    findings.append(
                        {
                            "source": "workspace",
                            "path": _item_field(item, "path", "name", "relpath"),
                            "name": _item_field(item, "name", "path", "relpath"),
                        }
                    )
            except Exception:
                pass

        longterm = getattr(ctx.memory, "longterm", None)
        recall = getattr(longterm, "recall", None) if longterm is not None else None
        if callable(recall):
            try:
                for entry in recall(query) or []:
                    if isinstance(entry, dict):
                        text = entry.get("text", "")
                        meta = {k: v for k, v in entry.items() if k != "text"}
                    else:
                        text = getattr(entry, "text", str(entry))
                        meta = {}
                    findings.append(
                        {"source": "memory", "text": text, "metadata": meta}
                    )
            except Exception:
                pass

        _mem_set(ctx, f"research:{_task_id(ctx.task)}", findings)
        # Aggregate key the planner agent reads; accumulate so multiple
        # research tasks in one goal do not clobber each other.
        prior = _mem_get(ctx, "research:findings", []) or []
        _mem_set(ctx, "research:findings", list(prior) + findings)

        return AgentResult(
            success=True,
            output={"findings": findings, "query": query},
            artifacts=[],
            message=f"researcher found {len(findings)} item(s) for query {query!r}",
        )
