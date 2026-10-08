"""Tester agent: runs pytest inside the sandbox and records a test report.

Executes ``[sys.executable, "-m", "pytest", test_file, "-q"]`` via
``ctx.sandbox.run`` (never directly — execution always goes through the
sandbox jail), parses the ``"N passed"`` / ``"M failed"`` summary lines,
writes ``TEST_REPORT.json`` to the workspace, and reports success only when
the pytest process exits 0.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from casi.agents.base import Agent, AgentContext, AgentResult, require_capability

_PASSED_RE = re.compile(r"(\d+)\s+passed")
_FAILED_RE = re.compile(r"(\d+)\s+failed")
_STDOUT_TAIL_CHARS = 2000
REPORT_FILENAME = "TEST_REPORT.json"


def _parse_counts(text: str) -> tuple[int, int]:
    """Extract (passed, failed) from pytest summary output.

    Uses the last occurrence of each pattern, which is the final summary
    line pytest prints.
    """
    passed_matches = _PASSED_RE.findall(text or "")
    failed_matches = _FAILED_RE.findall(text or "")
    passed = int(passed_matches[-1]) if passed_matches else 0
    failed = int(failed_matches[-1]) if failed_matches else 0
    return passed, failed


class TesterAgent(Agent):
    """Runs a pytest file in the sandbox and writes a JSON test report."""

    name = "tester"
    capabilities = ("test",)

    def run(self, ctx: AgentContext) -> AgentResult:
        """Run pytest for ``params["test_file"]`` and write the report."""
        params = getattr(ctx.task, "params", None) or {}
        if not isinstance(params, dict):
            params = {}
        test_file = params.get("test_file")
        timeout_s = params.get("timeout_s", 120)
        if not test_file:
            return AgentResult(
                success=False,
                output={},
                artifacts=[],
                message="tester: params['test_file'] is required",
                error="missing test_file",
            )

        # Workspace root for this goal: explicit param wins, else the
        # workspace's own root, else the current directory.
        cwd: Any = params.get("work_dir") or getattr(ctx.workspace, "root", None) or "."

        cmd = [sys.executable, "-m", "pytest", test_file, "-q"]
        require_capability(ctx, "EXECUTE_CODE")
        result = ctx.sandbox.run(cmd, cwd=cwd, timeout_s=timeout_s)

        combined = "\n".join(
            part for part in (result.stdout, result.stderr) if part
        )
        passed, failed = _parse_counts(combined)

        report = {
            "test_file": test_file,
            "passed": passed,
            "failed": failed,
            "returncode": result.returncode,
            "timed_out": bool(getattr(result, "timed_out", False)),
            "stdout_tail": (result.stdout or "")[-_STDOUT_TAIL_CHARS:],
        }
        require_capability(ctx, "WRITE_WORKSPACE")
        ctx.workspace.write(REPORT_FILENAME, json.dumps(report, indent=2), author="tester")

        success = result.returncode == 0
        summary = f"{passed} passed, {failed} failed (exit {result.returncode})"
        return AgentResult(
            success=success,
            output={
                "test_file": test_file,
                "passed": passed,
                "failed": failed,
                "returncode": result.returncode,
                "timed_out": report["timed_out"],
            },
            artifacts=[test_file, REPORT_FILENAME],
            message=f"tester ran {test_file}: {summary}",
            error=None if success else f"pytest exited {result.returncode}: {summary}",
        )
