"""Coder agent: writes code and test artifacts into the workspace.

Honest, documented behavior (ARCHITECTURE.md section 5): the first-pass
implementation emitted for ``kind="implementation"`` contains a genuine,
classic bug — ``return lst.sort()`` (``list.sort()`` sorts in place and
returns ``None``). The tester then genuinely fails, the debugger genuinely
patches, and the loop re-verifies. Nothing is hardcoded to pass.

The agent first asks the model provider (``ctx.models.complete("code", ...)``)
for the artifact text; the mock provider returns deterministic text per the
contract. If the provider is missing, raises, or returns unusable text (as
with test fakes), the agent falls back to built-in deterministic generation,
which is exactly the buggy-first-pass / correct-on-repair behavior above.

Repair mode is entered when ``ctx.task.params["attempt"] == "repair"`` or
the working-memory key ``f"repair:{task.id}"`` is set; in that mode the
correct implementation (``return sorted(lst)`` with a ``TypeError`` guard)
is emitted.

Permission gating: the workspace write goes through
:func:`casi.agents.base.require_capability` (``WRITE_WORKSPACE``) and fails
closed with ``PermissionDenied`` when the context carries a role that lacks
it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from casi.agents.base import Agent, AgentContext, AgentResult, require_capability


def _task_params(task: Any) -> dict:
    """Return the task's params dict, tolerating fakes without one."""
    params = getattr(task, "params", None)
    return dict(params) if isinstance(params, dict) else {}


def _task_id(task: Any) -> str:
    return str(getattr(task, "id", "unknown"))


def _mem_get(ctx: AgentContext, key: str, default: Any = None) -> Any:
    """Best-effort working-memory read.

    Prefers the real ``MemorySystem.working.get(goal_id, key)`` shape, then
    falls back to a flat ``memory.get(key)`` shape used by test fakes.
    Never raises.
    """
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
            actor="coder",
            details=details or {},
        )
    except Exception:
        pass


class CoderAgent(Agent):
    """Writes implementation and test files into the workspace."""

    name = "coder"
    capabilities = ("code",)

    # ------------------------------------------------------------------
    # deterministic generation (fallback + documented honest behavior)
    # ------------------------------------------------------------------
    def _implementation_code(self, function_name: str, repair_mode: bool) -> str:
        """Return the deterministic implementation source.

        First pass contains the genuine ``return lst.sort()`` bug
        (``list.sort()`` returns ``None``); repair mode emits the correct
        ``return sorted(lst)`` version with a ``TypeError`` guard.
        """
        doc = '"""Sort a list of numbers in ascending order."""'
        if not repair_mode:
            return (
                f"def {function_name}(lst):\n"
                f"    {doc}\n"
                f"    return lst.sort()\n"
            )
        return (
            f"def {function_name}(lst):\n"
            f'    """Sort a list of numbers in ascending order.\n\n'
            f"    Returns a new sorted list; the input is not modified.\n"
            f'    """\n'
            f"    if not isinstance(lst, list):\n"
            f'        raise TypeError(f"expected a list, got {{type(lst).__name__}}")\n'
            f"    return sorted(lst)\n"
        )

    def _tests_code(self, function_name: str, module_name: str) -> str:
        """Return a deterministic pytest module with real test functions."""
        fn = function_name
        return (
            f'"""Unit tests for {module_name}.{fn}."""\n'
            f"from {module_name} import {fn}\n"
            f"\n\n"
            f"def test_empty_list():\n"
            f"    assert {fn}([]) == []\n"
            f"\n\n"
            f"def test_already_sorted():\n"
            f"    assert {fn}([1, 2, 3, 4]) == [1, 2, 3, 4]\n"
            f"\n\n"
            f"def test_reverse_sorted():\n"
            f"    assert {fn}([5, 4, 3, 2, 1]) == [1, 2, 3, 4, 5]\n"
            f"\n\n"
            f"def test_duplicates_and_negatives():\n"
            f"    assert {fn}([3, -1, 2, -1, 3, 0]) == [-1, -1, 0, 2, 3, 3]\n"
            f"\n\n"
            f"def test_returns_new_list_and_does_not_mutate():\n"
            f"    data = [3, 1, 2]\n"
            f"    original = list(data)\n"
            f"    result = {fn}(data)\n"
            f"    assert isinstance(result, list)\n"
            f"    assert result == [1, 2, 3]\n"
            f"    assert data == original\n"
        )

    def _provider_code(self, ctx: AgentContext, kind: str, params: dict) -> str | None:
        """Ask the model provider for artifact text; None if unusable."""
        complete = getattr(ctx.models, "complete", None)
        if not callable(complete):
            return None
        function_name = params.get("function_name", "sort_list")
        goal_hint = params.get("goal_hint", "")
        system = (
            "You are a code-writing agent. Output only the requested Python "
            f"source code, no explanations. Goal: {goal_hint}"
        )
        if kind == "implementation":
            target = params.get("target_file", "")
            user = (
                f"Write a Python module defining {function_name}(lst) that "
                f"sorts a list of numbers ascending. This will be saved as "
                f"{target}."
            )
        else:
            user = (
                f"Write a pytest module testing {function_name} "
                f"(empty list, sorted input, reverse-sorted input, "
                f"duplicates/negatives, return-type and non-mutation checks)."
            )
        try:
            completion = complete("code", system, user)
        except Exception:
            return None
        text = getattr(completion, "text", None)
        if isinstance(text, str) and "def " in text:
            return _strip_code_fences(text)
        return None

    # ------------------------------------------------------------------
    def run(self, ctx: AgentContext) -> AgentResult:
        """Write the requested artifact and report the file produced."""
        params = _task_params(ctx.task)
        task_id = _task_id(ctx.task)
        kind = params.get("kind", "implementation")
        function_name = params.get("function_name", "sort_list")
        target_file = params.get("target_file")
        test_file = params.get("test_file")

        repair_mode = params.get("attempt") == "repair" or bool(
            _mem_get(ctx, f"repair:{task_id}")
        )

        if kind == "implementation":
            written_file = target_file
            if not written_file:
                return AgentResult(
                    success=False,
                    output={},
                    artifacts=[],
                    message="coder: kind='implementation' requires params['target_file']",
                    error="missing target_file",
                )
            code = self._provider_code(ctx, kind, params)
            if code is None:
                code = self._implementation_code(function_name, repair_mode)
        elif kind == "tests":
            written_file = test_file or target_file
            if not written_file:
                return AgentResult(
                    success=False,
                    output={},
                    artifacts=[],
                    message="coder: kind='tests' requires params['test_file']",
                    error="missing test_file",
                )
            code = self._provider_code(ctx, kind, params)
            if code is None:
                if target_file:
                    module_name = Path(target_file).stem
                else:
                    stem = Path(test_file).stem
                    module_name = stem[5:] if stem.startswith("test_") else stem
                code = self._tests_code(function_name, module_name)
        else:
            return AgentResult(
                success=False,
                output={},
                artifacts=[],
                message=f"coder: unknown kind {kind!r}",
                error=f"unknown kind {kind!r}",
            )

        require_capability(ctx, "WRITE_WORKSPACE")
        ctx.workspace.write(written_file, code, author="coder")
        _mem_set(ctx, f"code:{written_file}", "written")
        _audit(
            ctx,
            "agent.coder.wrote",
            {
                "file": written_file,
                "kind": kind,
                "function": function_name,
                "repair_mode": repair_mode,
            },
        )
        return AgentResult(
            success=True,
            output={"file": written_file, "kind": kind, "repair_mode": repair_mode},
            artifacts=[written_file],
            message=f"coder wrote {kind} artifact to {written_file}",
        )


def _strip_code_fences(text: str) -> str:
    """Remove Markdown code fences from model output, if present.

    Real LLMs habitually wrap code in ```python ... ``` even when asked not
    to; writing the fences verbatim would produce a SyntaxError artifact.
    Only strips a single leading/trailing fence pair.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        # Drop the opening fence (``` or ```python) ...
        lines = lines[1:]
        # ... and the closing fence, if present.
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip() + "\n"
    return text
