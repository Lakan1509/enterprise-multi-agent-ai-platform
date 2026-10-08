"""DAG scheduler: validation, topological levels, parallel execution.

``DAGScheduler`` validates a :class:`casi.planner.Plan`, computes deterministic
execution levels with Kahn's algorithm, and runs tasks level-by-level with
tasks inside a level in parallel. Retries use exponential backoff, checkpoints
persist per-level state for resume, and a ``threading.Event`` supports
cancellation.

The registry dependency is duck-typed: it only needs ``.find(capability)``
returning an object with ``.run(ctx)``. The concrete ``AgentRegistry`` /
``NoCapableAgent`` from ``casi.agents.base`` are imported lazily so this
module stays importable without them.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from casi.planner import Plan, TaskSpec

if TYPE_CHECKING:  # pragma: no cover - typing only
    from casi.agents.base import AgentContext


class PlanError(Exception):
    """Raised when a plan is invalid or the agent runtime is unavailable."""


@dataclass
class TaskOutcome:
    """The recorded result of one task execution."""

    task_id: str
    status: str  # "ok" | "failed" | "skipped" | "cancelled"
    attempts: int
    result: Any | None  # AgentResult when a run completed
    error: str | None = None


@dataclass
class RunReport:
    """Aggregate result of running a whole plan."""

    goal_id: str
    outcomes: list[TaskOutcome] = field(default_factory=list)
    success: bool = False
    started_at: str = ""
    finished_at: str = ""

    def outcome_for(self, task_id: str) -> TaskOutcome | None:
        """Return the outcome recorded for ``task_id``, or None."""
        for o in self.outcomes:
            if o.task_id == task_id:
                return o
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DAGScheduler:
    """Executes plans as a DAG with parallel levels, retries, and checkpoints."""

    def __init__(self, registry: Any, audit: Any, max_workers: int = 4) -> None:
        """Create a scheduler.

        Args:
            registry: Object with ``find(capability) -> agent`` where agent has
                ``run(ctx)``. Imported lazily from ``casi.agents.base`` at run
                time only when needed.
            audit: Audit log object with ``record(event, ...)``.
            max_workers: Parallel workers per level.
        """
        self.registry = registry
        self.audit = audit
        self.max_workers = max_workers

    # -- validation -------------------------------------------------------

    def validate(self, plan: Plan) -> None:
        """Validate ``plan``; raise :class:`PlanError` on any defect.

        Checks: duplicate task ids, dependencies on unknown tasks, self
        dependencies, and cycles.
        """
        ids = [t.id for t in plan.tasks]
        if len(set(ids)) != len(ids):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise PlanError(f"duplicate task ids: {dupes}")
        id_set = set(ids)
        for t in plan.tasks:
            if t.id in t.depends_on:
                raise PlanError(f"task {t.id!r} depends on itself")
            for dep in t.depends_on:
                if dep not in id_set:
                    raise PlanError(f"task {t.id!r} depends on unknown task {dep!r}")
        # Cycle detection via levels computation.
        self.levels(plan)

    # -- topological levels -------------------------------------------------

    def levels(self, plan: Plan) -> list[list[TaskSpec]]:
        """Compute execution levels with Kahn's algorithm.

        Each level lists tasks whose dependencies are all in earlier levels;
        tasks inside a level are sorted by id for determinism. Raises
        :class:`PlanError` if the graph has a cycle.
        """
        by_id = {t.id: t for t in plan.tasks}
        indegree = {t.id: 0 for t in plan.tasks}
        children: dict[str, list[str]] = {t.id: [] for t in plan.tasks}
        for t in plan.tasks:
            for dep in t.depends_on:
                if dep in by_id:  # unknown deps are a validate() error, not levels()
                    indegree[t.id] += 1
                    children[dep].append(t.id)

        levels: list[list[TaskSpec]] = []
        ready = sorted(tid for tid, deg in indegree.items() if deg == 0)
        seen = 0
        while ready:
            level = [by_id[tid] for tid in ready]
            levels.append(level)
            seen += len(ready)
            nxt: list[str] = []
            for tid in ready:
                for child in children[tid]:
                    indegree[child] -= 1
                    if indegree[child] == 0:
                        nxt.append(child)
            ready = sorted(nxt)
        if seen != len(plan.tasks):
            raise PlanError("plan contains a dependency cycle")
        return levels

    # -- checkpoints --------------------------------------------------------

    @staticmethod
    def _write_checkpoint(path: Path, plan: Plan, outcomes: list[TaskOutcome]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "goal_id": plan.goal_id,
            "task_ids": [t.id for t in plan.tasks],
            "outcomes": [
                {
                    "task_id": o.task_id,
                    "status": o.status,
                    "attempts": o.attempts,
                    "error": o.error,
                    # result payloads are agent-specific; persist message only
                    "result_message": getattr(o.result, "message", None),
                }
                for o in outcomes
            ],
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @staticmethod
    def _read_checkpoint(path: Path, plan: Plan) -> dict[str, TaskOutcome]:
        """Load prior outcomes if the checkpoint matches this plan; else {}."""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if payload.get("task_ids") != [t.id for t in plan.tasks]:
            return {}  # plan changed — start over
        done: dict[str, TaskOutcome] = {}
        for entry in payload.get("outcomes", []):
            done[entry["task_id"]] = TaskOutcome(
                task_id=entry["task_id"],
                status=entry.get("status", "failed"),
                attempts=entry.get("attempts", 0),
                result=None,
                error=entry.get("error"),
            )
        return done

    # -- execution ------------------------------------------------------------

    def _resolve_agent(self, capability: str) -> Any:
        """Find an agent for ``capability`` via the registry (lazy import)."""
        try:
            from casi.agents.base import NoCapableAgent  # noqa: F401 (import for side-effect check)
        except ImportError:
            # Fall through to duck-typed registry; the error path below still
            # produces a clear PlanError when nothing can handle the capability.
            pass
        try:
            return self.registry.find(capability)
        except Exception as exc:  # noqa: BLE001 - registry defines its own error type
            raise PlanError(f"agent runtime not available for capability {capability!r}: {exc}") from exc

    def _run_task(self, task: TaskSpec, ctx: Any) -> TaskOutcome:
        """Run one task with exponential-backoff retries."""
        agent = self._resolve_agent(task.agent_capability)
        attempts = 0
        last_error: str | None = None
        delay = 0.1
        max_attempts = max(1, task.max_retries + 1)
        while attempts < max_attempts:
            attempts += 1
            try:
                result = agent.run(ctx)
            except Exception as exc:  # noqa: BLE001 - wrap into a failed outcome
                last_error = f"{type(exc).__name__}: {exc}"
                result = None
            else:
                if getattr(result, "success", False):
                    return TaskOutcome(task.id, "ok", attempts, result)
                last_error = getattr(result, "error", None) or getattr(result, "message", None) or "agent reported failure"
            if attempts < max_attempts:
                time.sleep(delay)
                delay *= 2
        return TaskOutcome(task.id, "failed", attempts, None, last_error)

    def _audit(self, event: str, goal_id: str, task_id: str = "", details: dict | None = None) -> None:
        record = getattr(self.audit, "record", None)
        if record is not None:
            try:
                record(event, goal_id=goal_id, task_id=task_id, details=details or {})
            except Exception:  # noqa: BLE001 - audit must never break scheduling
                pass

    def run(
        self,
        plan: Plan,
        ctx_factory: Callable[[TaskSpec], Any],
        cancel_event: threading.Event | None = None,
        checkpoint_path: Path | None = None,
    ) -> RunReport:
        """Run ``plan`` to completion and return a :class:`RunReport`.

        Success semantics: the run succeeds when every task is ``ok``, except
        (a) conditional tasks skipped because their trigger did not fire, and
        (b) failed tasks for which the plan declared an explicit repair branch
        (``conditional_on_failure_of``) that later succeeded — a handled
        failure, e.g. tests fail → debugger repairs → tests re-run green.

        Args:
            plan: Validated plan (validated again on entry).
            ctx_factory: Callable producing an ``AgentContext`` per task.
            cancel_event: When set, remaining tasks become ``cancelled``.
            checkpoint_path: Optional JSON file written after each level and
                read on entry to skip already-``ok`` tasks from a previous run.
        """
        self.validate(plan)
        report = RunReport(goal_id=plan.goal_id, started_at=_now_iso())

        resumed: dict[str, TaskOutcome] = {}
        if checkpoint_path is not None and checkpoint_path.exists():
            resumed = self._read_checkpoint(checkpoint_path, plan)
        outcomes: dict[str, TaskOutcome] = dict(resumed)
        self._audit("run.started", plan.goal_id, details={"resumed": sorted(resumed)})

        levels = self.levels(plan)
        cancelled = False

        # Tasks that only run on a condition (e.g. repair on test failure):
        # id -> the task id whose non-ok outcome triggers them.
        conditional: dict[str, str] = {
            t.id: t.params.get("conditional_on_failure_of")
            for t in plan.tasks
            if t.params.get("conditional_on_failure_of")
        }

        def _cond_satisfied(dep_id: str) -> bool:
            """True when a conditional dep was skipped *because its trigger
            did not fire* — downstream work may proceed as if it succeeded."""
            trigger = conditional.get(dep_id)
            if not trigger:
                return False
            trigger_outcome = outcomes.get(trigger)
            dep_outcome = outcomes.get(dep_id)
            return (
                dep_outcome is not None
                and dep_outcome.status == "skipped"
                and trigger_outcome is not None
                and trigger_outcome.status == "ok"
            )

        def _dep_satisfied(dep_id: str) -> bool:
            outcome = outcomes.get(dep_id)
            if outcome is None:
                return False
            return outcome.status == "ok" or _cond_satisfied(dep_id)

        def _cancelled() -> bool:
            return cancel_event is not None and cancel_event.is_set()

        for level in levels:
            if _cancelled():
                cancelled = True
                break

            runnable: list[TaskSpec] = []
            for task in level:
                if task.id in outcomes:
                    continue  # checkpointed from a previous run
                cond = task.params.get("conditional_on_failure_of")
                if cond:
                    # Conditional tasks (e.g. t4 repair) run only when the
                    # named task did NOT succeed; this bypasses the normal
                    # dep-ok rule so repair can run after a failure.
                    cond_outcome = outcomes.get(cond)
                    if cond_outcome is not None:
                        if cond_outcome.status == "ok":
                            outcomes[task.id] = TaskOutcome(
                                task.id, "skipped", 0, None, f"conditional: {cond} succeeded"
                            )
                            self._audit(
                                "task.skipped", plan.goal_id, task.id,
                                {"reason": "conditional not triggered"},
                            )
                            continue
                        runnable.append(task)
                        continue
                    # No outcome for the named task yet: fall through to dep check.
                dep_outcomes = [outcomes.get(d) for d in task.depends_on]
                if any(not _dep_satisfied(d) for d in task.depends_on):
                    outcomes[task.id] = TaskOutcome(task.id, "skipped", 0, None, "dependency not ok")
                    self._audit("task.skipped", plan.goal_id, task.id, {"reason": "dependency not ok"})
                    continue
                runnable.append(task)

            if runnable:
                with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                    future_to_task = {
                        pool.submit(self._run_task, task, ctx_factory(task)): task for task in runnable
                    }
                    for future in as_completed(future_to_task):
                        task = future_to_task[future]
                        try:
                            outcome = future.result()
                        except Exception as exc:  # noqa: BLE001 - never lose a task
                            outcome = TaskOutcome(task.id, "failed", 0, None, f"scheduler error: {exc}")
                        outcomes[task.id] = outcome
                        self._audit(
                            "task.finished",
                            plan.goal_id,
                            task.id,
                            {"status": outcome.status, "attempts": outcome.attempts},
                        )
                        if _cancelled():
                            cancelled = True
                            break

            if checkpoint_path is not None:
                self._write_checkpoint(checkpoint_path, plan, [outcomes[t.id] for t in plan.tasks if t.id in outcomes])

            if cancelled:
                break

        # Mark anything never attempted as cancelled/skipped.
        for task in plan.tasks:
            if task.id not in outcomes:
                status = "cancelled" if cancelled or _cancelled() else "skipped"
                outcomes[task.id] = TaskOutcome(task.id, status, 0, None, "not attempted")

        report.outcomes = [outcomes[t.id] for t in plan.tasks]
        report.finished_at = _now_iso()
        # A conditional task skipped because its trigger did not fire (e.g.
        # no repair needed) does not fail the run. Likewise, a failed task
        # counts as *recovered* when the plan explicitly declared a repair
        # branch for it (conditional_on_failure_of) and that branch later
        # succeeded — the failure was handled, not ignored.
        trigger_branches: dict[str, list[str]] = {}
        for t in plan.tasks:
            trigger = t.params.get("conditional_on_failure_of")
            if trigger:
                trigger_branches.setdefault(trigger, []).append(t.id)

        def _recovered(task_id: str) -> bool:
            outcome = outcomes.get(task_id)
            if outcome is None or outcome.status != "failed":
                return False
            return any(
                (branch := outcomes.get(branch_id)) is not None and branch.status == "ok"
                for branch_id in trigger_branches.get(task_id, [])
            )

        report.success = all(
            o.status == "ok" or _cond_satisfied(o.task_id) or _recovered(o.task_id)
            for o in report.outcomes
        )
        self._audit("run.finished", plan.goal_id, details={"success": report.success})
        return report
