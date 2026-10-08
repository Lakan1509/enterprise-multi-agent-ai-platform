"""Reviewer agent: verifies artifacts before they are accepted/published.

Real checks, no stubs:

- every listed file exists in the workspace and is non-empty;
- every ``.py`` file compiles via the builtin ``compile()`` (real syntax check);
- when a test report dict is supplied (``params["test_report"]``), it must
  show ``failed == 0``.

Publish flow: when ``params["action"] == "publish"`` (or the task itself is
``approval_required``), the reviewer does NOT decide — it requests a
``"publish"`` approval through ``ctx.approvals`` and returns immediately
with the approval id. The kernel waits on the approval gate; the reviewer
never blocks here.
"""

from __future__ import annotations

from typing import Any

from casi.agents.base import Agent, AgentContext, AgentResult, require_capability


def _task_params(task: Any) -> dict:
    params = getattr(task, "params", None)
    return dict(params) if isinstance(params, dict) else {}


class ReviewerAgent(Agent):
    """Reviews workspace artifacts: existence, syntax, and test results."""

    name = "reviewer"
    capabilities = ("review",)

    def run(self, ctx: AgentContext) -> AgentResult:
        """Review ``params["files"]`` or request publish approval."""
        params = _task_params(ctx.task)
        files = params.get("files", [])
        if isinstance(files, str):
            files = [files]
        files = list(files or [])

        # Publish path: request approval instead of deciding.
        wants_publish = params.get("action") == "publish" or bool(
            getattr(ctx.task, "approval_required", False)
        )
        if wants_publish:
            details = {"goal_id": getattr(ctx, "goal_id", ""), "files": files}
            approval = ctx.approvals.request("publish", details)
            approval_id = getattr(approval, "id", None)
            return AgentResult(
                success=True,
                output={"approval_id": approval_id},
                artifacts=[],
                message="publish awaiting approval",
            )

        checks: dict[str, dict[str, Any]] = {}
        all_passed = True

        if files:
            require_capability(ctx, "READ_WORKSPACE")

        for relpath in files:
            entry: dict[str, Any] = {
                "exists": False,
                "non_empty": False,
                "compiles": None,
                "error": None,
            }
            content: str | None = None
            try:
                entry["exists"] = bool(ctx.workspace.exists(relpath))
            except Exception:
                entry["exists"] = False
            if entry["exists"]:
                try:
                    content = ctx.workspace.read(relpath)
                    entry["non_empty"] = bool(content and content.strip())
                except Exception as exc:
                    entry["error"] = str(exc)
            if relpath.endswith(".py") and entry["non_empty"] and content is not None:
                try:
                    compile(content, relpath, "exec")
                    entry["compiles"] = True
                except SyntaxError as exc:
                    entry["compiles"] = False
                    entry["error"] = f"SyntaxError: {exc}"
            entry["passed"] = bool(
                entry["exists"]
                and entry["non_empty"]
                and entry["compiles"] is not False
            )
            if not entry["passed"]:
                all_passed = False
            checks[relpath] = entry

        test_report = params.get("test_report")
        if isinstance(test_report, dict):
            failed = test_report.get("failed", 0)
            report_ok = failed == 0
            checks["test_report"] = {
                "failed": failed,
                "passed": report_ok,
                "error": None if report_ok else f"{failed} test(s) failed",
            }
            if not report_ok:
                all_passed = False

        if all_passed:
            message = f"review passed for {len(files)} file(s)"
            error = None
        else:
            bad = [k for k, v in checks.items() if not v.get("passed")]
            message = f"review failed: {', '.join(bad)}"
            error = message

        return AgentResult(
            success=all_passed,
            output={"checks": checks},
            artifacts=[],
            message=message,
            error=error,
        )
