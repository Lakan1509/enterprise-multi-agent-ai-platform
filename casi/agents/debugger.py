"""Debugger agent: diagnoses test failures and applies real source patches.

Repair strategies are rule-based and applied in order; the first match wins:

1. ``return <expr>.sort()`` in the source plus failure output mentioning
   ``None``/``NoneType`` or an assertion mismatch -> replace with
   ``return sorted(<expr>)`` (the classic in-place-sort bug the coder emits
   on its first pass).
2. ``NameError: name 'X' is not defined`` where ``X`` is a stdlib module in
   a small allowlist -> prepend ``import X``.
3. Off-by-one loop bound plus an ``IndexError`` in the failure output:
   ``range(len(X) + 1)`` -> ``range(len(X))``, and
   ``while i <= len(X):`` -> ``while i < len(X):``.
4. Wrong return type: ``return str(<expr>)`` plus an ``AssertionError`` in
   the failure output -> drop the spurious coercion, ``return <expr>``.
5. Fallback: no applicable strategy — record ``repair:requested`` in working
   memory and return ``success=False`` with an honest error so the scheduler
   can retry or fail the goal. Nothing is faked to pass.

Every strategy is verified end-to-end in ``tests/test_debugger.py`` against
a *real* broken program: pytest is genuinely executed, the real failure
output is fed to the debugger, and the patched file is re-executed to prove
the tests now pass.

The patched file is written back through the workspace (creating a new
version), and the strategy name is recorded in the audit log and in working
memory under ``f"repair:{target_file}"`` (and ``f"repair:{task.id}"``) so the
coder/tests can see that a repair happened.

Permission gating: reading the target file requires ``READ_WORKSPACE`` and
writing the patch requires ``WRITE_WORKSPACE`` (via
:func:`casi.agents.base.require_capability`); both fail closed with
``PermissionDenied``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from casi.agents.base import Agent, AgentContext, AgentResult, require_capability

# Strategy 1: the coder's first-pass bug — list.sort() returns None.
_INPLACE_SORT_RE = re.compile(r"return\s+(\w[\w.]*)\.sort\(\)")
# Strategy 2: missing stdlib import.
_NAME_ERROR_RE = re.compile(r"NameError:\s*name\s+'(\w+)'\s+is\s+not\s+defined")
_IMPORT_LINE_RE_TEMPLATE = r"^\s*(?:import\s+{name}\b|from\s+{name}\b)"
# Strategy 3: off-by-one loop bounds.
_OFF_BY_ONE_RANGE_RE = re.compile(r"range\(\s*len\(\s*([A-Za-z_]\w*)\s*\)\s*\+\s*1\s*\)")
_OFF_BY_ONE_WHILE_RE = re.compile(
    r"while\s+([A-Za-z_]\w*)\s*<=\s*len\(\s*([A-Za-z_]\w*)\s*\)\s*:"
)
# Strategy 4: spurious str() coercion on a returned value (line-based match).
_STR_RETURN_PREFIX = "return str("

#: Stdlib modules the debugger is willing to auto-import (strategy 2).
STDLIB_IMPORT_ALLOWLIST = frozenset({"math", "json", "re", "os", "sys", "statistics"})

_NO_STRATEGY_ERROR = "no applicable repair strategy"


def _task_params(task: Any) -> dict:
    params = getattr(task, "params", None)
    return dict(params) if isinstance(params, dict) else {}


def _task_id(task: Any) -> str:
    return str(getattr(task, "id", "unknown"))


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


def _audit(ctx: AgentContext, event: str, details: dict | None = None) -> None:
    """Best-effort audit record. Never raises."""
    audit = getattr(ctx, "audit", None)
    record = getattr(audit, "record", None) if audit is not None else None
    if not callable(record):
        return
    try:
        record(
            event,
            goal_id=getattr(ctx, "goal_id", ""),
            task_id=_task_id(ctx.task),
            actor="debugger",
            details=details or {},
        )
    except Exception:
        pass


def _load_report(ctx: AgentContext, test_report: Any) -> dict | None:
    """Accept a report dict or a workspace path to TEST_REPORT.json."""
    if isinstance(test_report, dict):
        return test_report
    if isinstance(test_report, str) and test_report:
        try:
            return json.loads(ctx.workspace.read(test_report))
        except Exception:
            return None
    return None


def _report_text(report: dict) -> str:
    """Combine every textual field of the report for pattern matching."""
    parts: list[str] = []
    for key in ("stdout_tail", "stdout", "stderr", "output", "error"):
        value = report.get(key)
        if value:
            parts.append(str(value))
    failures = report.get("failures")
    if isinstance(failures, list):
        parts.extend(str(f) for f in failures)
    return "\n".join(parts)


class DebuggerAgent(Agent):
    """Parses real test failures and patches the target source file."""

    name = "debugger"
    capabilities = ("debug",)

    def run(self, ctx: AgentContext) -> AgentResult:
        """Diagnose the failing report and patch ``params["target_file"]``."""
        params = _task_params(ctx.task)
        task_id = _task_id(ctx.task)
        target_file = params.get("target_file")
        test_file = params.get("test_file")

        if not target_file:
            return AgentResult(
                success=False,
                output={},
                artifacts=[],
                message="debugger: params['target_file'] is required",
                error="missing target_file",
            )

        report = _load_report(ctx, params.get("test_report"))
        if report is None:
            return AgentResult(
                success=False,
                output={},
                artifacts=[target_file],
                message="debugger: could not load test report",
                error="missing or unreadable test_report",
            )

        report_text = _report_text(report)
        failing_tests = re.findall(r"FAILED\s+(\S+)", report_text)

        require_capability(ctx, "READ_WORKSPACE")
        try:
            source = ctx.workspace.read(target_file)
        except Exception as exc:
            return AgentResult(
                success=False,
                output={"failures": failing_tests},
                artifacts=[target_file],
                message=f"debugger: could not read {target_file}",
                error=str(exc),
            )

        strategy: str | None = None
        patched: str | None = None
        lowered = report_text.lower()

        # Strategy 1: in-place sort returning None.
        sort_match = _INPLACE_SORT_RE.search(source)
        if sort_match and ("none" in lowered or "assert" in lowered):
            expr = sort_match.group(1)
            patched = (
                source[: sort_match.start()]
                + f"return sorted({expr})"
                + source[sort_match.end() :]
            )
            strategy = "replace-inplace-sort-with-sorted"

        # Strategy 2: missing stdlib import.
        if strategy is None:
            name_match = _NAME_ERROR_RE.search(report_text)
            if name_match:
                missing = name_match.group(1)
                already = re.search(
                    _IMPORT_LINE_RE_TEMPLATE.format(name=re.escape(missing)),
                    source,
                    re.MULTILINE,
                )
                if missing in STDLIB_IMPORT_ALLOWLIST and not already:
                    patched = f"import {missing}\n" + source
                    strategy = f"add-missing-import:{missing}"

        # Strategy 3: off-by-one loop bound (IndexError in the report).
        if strategy is None and "indexerror" in lowered:
            range_match = _OFF_BY_ONE_RANGE_RE.search(source)
            while_match = _OFF_BY_ONE_WHILE_RE.search(source)
            if range_match:
                seq = range_match.group(1)
                patched = (
                    source[: range_match.start()]
                    + f"range(len({seq}))"
                    + source[range_match.end() :]
                )
                strategy = "fix-off-by-one-range-bound"
            elif while_match:
                index_var, seq = while_match.group(1), while_match.group(2)
                patched = (
                    source[: while_match.start()]
                    + f"while {index_var} < len({seq}):"
                    + source[while_match.end() :]
                )
                strategy = "fix-off-by-one-while-bound"

        # Strategy 4: spurious str() coercion on the return value.
        if strategy is None and "assertionerror" in lowered:
            lines = source.splitlines(keepends=True)
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith(_STR_RETURN_PREFIX) and stripped.endswith(")"):
                    inner = stripped[len(_STR_RETURN_PREFIX) : -1]
                    lines[i] = line.replace(
                        f"{_STR_RETURN_PREFIX}{inner})", f"return {inner}", 1
                    )
                    patched = "".join(lines)
                    strategy = "remove-spurious-str-coercion"
                    break

        if strategy is not None and patched is not None:
            require_capability(ctx, "WRITE_WORKSPACE")
            ctx.workspace.write(target_file, patched, author="debugger")
            _mem_set(ctx, f"repair:{target_file}", strategy)
            _mem_set(ctx, f"repair:{task_id}", strategy)
            _audit(
                ctx,
                "agent.debugger.patched",
                {"file": target_file, "strategy": strategy, "test_file": test_file},
            )
            return AgentResult(
                success=True,
                output={
                    "file": target_file,
                    "strategy": strategy,
                    "failures": failing_tests,
                },
                artifacts=[target_file],
                message=f"debugger patched {target_file} via strategy '{strategy}'",
            )

        # Fallback: honest failure — let the scheduler retry/fail.
        _mem_set(ctx, "repair:requested", target_file)
        _audit(
            ctx,
            "agent.debugger.no_strategy",
            {"file": target_file, "failures": failing_tests},
        )
        return AgentResult(
            success=False,
            output={"file": target_file, "failures": failing_tests},
            artifacts=[target_file],
            message=f"debugger found no repair strategy for {target_file}",
            error=_NO_STRATEGY_ERROR,
        )
