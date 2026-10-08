"""Planner agent: turns research findings into a concrete execution outline.

Rule-based and deterministic. It reads the research findings stored in
working memory (written by the researcher agent under ``"research:findings"``)
and produces a short, ordered outline plus a structured task list —
persisted both to working memory (``"plan:outline"`` / ``"plan:tasks"``) and
to the workspace as ``PLAN.md`` — so downstream agents and humans can see
what will be executed. This is genuine, inspectable logic; it does not
pretend to reason like an LLM.

Permission gating: writing ``PLAN.md`` goes through
:func:`casi.agents.base.require_capability` (``WRITE_WORKSPACE``) and fails
closed with ``PermissionDenied`` when the context carries a role that lacks
it.
"""

from __future__ import annotations

from typing import Any

from casi.agents.base import Agent, AgentContext, AgentResult, require_capability


def _mem_get(ctx: AgentContext, key: str, default: Any = None) -> Any:
    mem = getattr(ctx, "memory", None)
    working = getattr(mem, "working", None) if mem is not None else None
    getter = getattr(working, "get", None) if working is not None else None
    if callable(getter):
        try:
            return getter(ctx.goal_id, key, default)
        except Exception:
            return default
    return default


def _mem_set(ctx: AgentContext, key: str, value: Any) -> None:
    mem = getattr(ctx, "memory", None)
    working = getattr(mem, "working", None) if mem is not None else None
    setter = getattr(working, "set", None) if working is not None else None
    if callable(setter):
        try:
            setter(ctx.goal_id, key, value)
        except Exception:
            pass


def _audit(ctx: AgentContext, event: str, details: dict | None = None) -> None:
    audit = getattr(ctx, "audit", None)
    record = getattr(audit, "record", None) if audit is not None else None
    if callable(record):
        try:
            record(
                event,
                goal_id=getattr(ctx, "goal_id", ""),
                task_id=str(getattr(getattr(ctx, "task", None), "id", "")),
                actor="planner",
                details=details or {},
            )
        except Exception:
            pass


class PlannerAgent(Agent):
    """Refines research findings into an ordered execution outline."""

    name = "planner"
    capabilities = ("plan",)

    def run(self, ctx: AgentContext) -> AgentResult:
        """Build the outline and task list from working-memory findings."""
        params = getattr(ctx.task, "params", None) or {}
        goal_hint = params.get("goal_hint", "")
        findings = _mem_get(ctx, "research:findings", []) or []

        if findings:
            tasks = [
                {
                    "order": i,
                    "title": f"Act on finding {i}",
                    "detail": str(finding),
                }
                for i, finding in enumerate(findings, 1)
            ]
        else:
            tasks = [
                {
                    "order": 1,
                    "title": "Clarify the goal",
                    "detail": (
                        "No research findings were recorded; confirm scope "
                        "before executing."
                    ),
                }
            ]

        lines = ["# Execution outline", "", f"Goal: {goal_hint}", ""]
        if findings:
            lines.append("## Research findings")
            for i, finding in enumerate(findings, 1):
                lines.append(f"{i}. {finding}")
            lines.append("")
        lines.append("## Task list")
        for task in tasks:
            lines.append(f"{task['order']}. {task['title']}: {task['detail']}")
        outline = "\n".join(lines) + "\n"

        _mem_set(ctx, "plan:outline", outline)
        _mem_set(ctx, "plan:tasks", tasks)

        require_capability(ctx, "WRITE_WORKSPACE")
        artifact = "PLAN.md"
        try:
            ctx.workspace.write(artifact, outline, author="planner")
            artifacts = [artifact]
        except Exception:
            artifacts = []

        _audit(
            ctx,
            "agent.planner.outline",
            {"steps": len(tasks), "artifact": artifact},
        )
        return AgentResult(
            success=True,
            output={"outline": outline, "steps": len(tasks), "tasks": tasks},
            artifacts=artifacts,
            message=f"planner wrote outline with {len(tasks)} task(s)",
        )
