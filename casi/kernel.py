"""AIKernel — composition root and goal lifecycle manager.

The kernel wires every CASI component and owns the goal lifecycle:
``create_goal → run_goal (plan → schedule → execute → verify → approval gate) → done``.

Component modules built by other builders (memory, models, security,
filesystem, execution, agents) may not exist yet. Every one of them is
imported lazily inside :meth:`AIKernel.__init__` with ``try/except
ImportError``: missing components are stored as ``None`` and a clear
``RuntimeError`` is raised only when the missing component is actually used.
This keeps the kernel importable and unit-testable with stub/fake
collaborators assigned onto its public attributes.
"""

from __future__ import annotations

import importlib
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import SimpleNamespace
from typing import Any

from casi.config import Settings
from casi.planner import Plan, TaskSpec, decompose_goal
from casi.scheduler import DAGScheduler, RunReport


class GoalStatus(str, Enum):
    """Lifecycle states of a goal."""

    CREATED = "created"
    PLANNED = "planned"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GoalNotFound(Exception):
    """Raised when a goal id is unknown to the kernel."""


class GoalStateError(Exception):
    """Raised when an operation is invalid for the goal's current status."""


@dataclass
class Goal:
    """A tracked unit of work."""

    id: str
    text: str
    status: GoalStatus
    created_at: str
    plan: Plan | None = None
    report: RunReport | None = None
    artifacts: list[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _try_import(module_path: str, attr: str) -> Any | None:
    """Import ``attr`` from ``module_path``; return None when unavailable."""
    try:
        module = importlib.import_module(module_path)
    except ImportError:
        return None
    return getattr(module, attr, None)


class AIKernel:
    """Composition root: owns all components and drives the goal lifecycle.

    Public component attributes (None when the owning module is not built
    yet): ``settings``, ``workspace``, ``memory``, ``models`` (router),
    ``cost``, ``sandbox``, ``registry``, ``supervisor``, ``scheduler``,
    ``approvals``, ``audit``.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """Wire components, lazily tolerating modules not built yet."""
        self.settings = settings or Settings()
        self.settings.ensure_dirs()

        audit_cls = _try_import("casi.security.audit", "AuditLog")
        self.audit = audit_cls(self.settings.audit_path) if audit_cls else None

        workspace_cls = _try_import("casi.filesystem.workspace", "Workspace")
        self.workspace = workspace_cls(self.settings.resolved_workspace_dir) if workspace_cls else None

        memory_cls = _try_import("casi.memory.store", "MemorySystem")
        self.memory = memory_cls(self.settings.data_dir) if memory_cls else None

        router_cls = _try_import("casi.models.providers", "ModelRouter")
        mock_cls = _try_import("casi.models.providers", "MockProvider")
        self.models = None
        if router_cls is not None:
            try:
                providers: dict[str, Any] = {}
                if mock_cls is not None:
                    mock = mock_cls()
                    providers[getattr(mock, "name", "mock")] = mock
                default = self.settings.default_provider or "mock"
                if default not in providers and providers:
                    default = next(iter(providers))
                self.models = router_cls(providers=providers, default=default) if providers else None
            except Exception:  # noqa: BLE001 - router construction must not break the kernel
                self.models = None

        cost_cls = _try_import("casi.models.providers", "CostTracker")
        self.cost = cost_cls() if cost_cls else None

        sandbox_cls = _try_import("casi.execution.sandbox", "Sandbox")
        self.sandbox = sandbox_cls(self.settings.resolved_workspace_dir) if sandbox_cls else None

        registry_cls = _try_import("casi.agents.base", "AgentRegistry")
        self.registry = registry_cls() if registry_cls else None
        self._register_builtin_agents()

        supervisor_cls = _try_import("casi.agents.supervisor", "SupervisorAgent")
        self.supervisor = supervisor_cls() if supervisor_cls else None

        self.scheduler = DAGScheduler(self.registry, self.audit, max_workers=self.settings.max_workers)

        approvals_cls = _try_import("casi.security.approvals", "ApprovalGate")
        self.approvals = approvals_cls() if approvals_cls else None

        self._goals: dict[str, Goal] = {}
        self._cancel_events: dict[str, threading.Event] = {}

    def _register_builtin_agents(self) -> None:
        """Register the built-in agent set; tolerates missing modules."""
        if self.registry is None:
            return
        for module_path, attr in (
            ("casi.agents.supervisor", "SupervisorAgent"),
            ("casi.agents.coder", "CoderAgent"),
            ("casi.agents.tester", "TesterAgent"),
            ("casi.agents.debugger", "DebuggerAgent"),
            ("casi.agents.reviewer", "ReviewerAgent"),
            ("casi.agents.researcher", "ResearcherAgent"),
            ("casi.agents.planner_agent", "PlannerAgent"),
        ):
            cls = _try_import(module_path, attr)
            if cls is None:
                continue
            try:
                self.registry.register(cls())
            except ValueError:
                pass  # already registered

    # -- internal helpers -------------------------------------------------

    def _require(self, name: str) -> Any:
        """Return component ``name`` or raise a clear RuntimeError."""
        component = getattr(self, name, None)
        if component is None:
            raise RuntimeError(
                f"CASI component {name!r} is not available: its module has not "
                "been built/imported yet."
            )
        return component

    def _audit(self, event: str, goal: Goal, details: dict | None = None) -> None:
        if self.audit is not None:
            self.audit.record(event, goal_id=goal.id, details=details or {})

    def _transition(self, goal: Goal, status: GoalStatus, event: str, details: dict | None = None) -> None:
        goal.status = status
        self._audit(event, goal, details)

    def _make_ctx_factory(self, goal: Goal):
        """Build ``ctx_factory(task)`` wiring all components into a context."""
        agent_context_cls = _try_import("casi.agents.base", "AgentContext")

        def factory(task: TaskSpec):
            kwargs = {
                "goal_id": goal.id,
                "task": task,
                "workspace": self.workspace,
                "memory": self.memory,
                "models": self.models,
                "sandbox": self.sandbox,
                "audit": self.audit,
                "approvals": self.approvals,
            }
            if agent_context_cls is not None:
                return agent_context_cls(**kwargs)
            # Stand-in while casi.agents.base is not built yet.
            return SimpleNamespace(**kwargs)

        return factory

    # -- goal API -----------------------------------------------------------

    def create_goal(self, text: str, requester: str = "api") -> Goal:
        """Create a goal; raises ``ValueError`` on empty text."""
        if not text or not text.strip():
            raise ValueError("goal text must be non-empty")
        goal = Goal(
            id=uuid.uuid4().hex[:12],
            text=text.strip(),
            status=GoalStatus.CREATED,
            created_at=_now_iso(),
        )
        self._goals[goal.id] = goal
        self._audit("goal.created", goal, {"requester": requester})
        return goal

    def get_goal(self, goal_id: str) -> Goal:
        """Return the goal; raises :class:`GoalNotFound` when unknown."""
        try:
            return self._goals[goal_id]
        except KeyError:
            raise GoalNotFound(f"unknown goal id: {goal_id!r}") from None

    def list_goals(self) -> list[Goal]:
        """Return all goals in creation order."""
        return list(self._goals.values())

    def run_goal(self, goal_id: str, auto_approve: bool = False) -> Goal:
        """Run a goal end-to-end: plan → schedule → verify → approval gate.

        Any exception during the run marks the goal ``FAILED`` (with an audit
        entry) and the goal is returned. Cancellation via
        :meth:`cancel_goal` marks remaining work cancelled.
        """
        goal = self.get_goal(goal_id)
        if goal.status not in (GoalStatus.CREATED, GoalStatus.FAILED):
            raise GoalStateError(f"cannot run goal in status {goal.status.value!r}")

        cancel_event = threading.Event()
        self._cancel_events[goal_id] = cancel_event
        try:
            self._transition(goal, GoalStatus.PLANNED, "goal.planned")
            goal.plan = decompose_goal(goal.text, goal.id)
            self._audit("goal.decomposed", goal, {"tasks": goal.plan.task_ids()})

            self._require("scheduler")
            self._require("registry")
            self._require("approvals")
            self._require("workspace")
            self._require("audit")
            self.scheduler.registry = self.registry
            self.scheduler.audit = self.audit

            self._transition(goal, GoalStatus.RUNNING, "goal.running")
            checkpoint_path = self.settings.runs_dir / goal.id / "checkpoint.json"
            report = self.scheduler.run(
                goal.plan,
                self._make_ctx_factory(goal),
                cancel_event=cancel_event,
                checkpoint_path=checkpoint_path,
            )
            goal.report = report

            artifacts: list[str] = []
            for outcome in report.outcomes:
                result = outcome.result
                if result is not None:
                    artifacts.extend(getattr(result, "artifacts", None) or [])
            goal.artifacts = sorted(set(artifacts))
            self._audit("goal.executed", goal, {"success": report.success, "artifacts": goal.artifacts})

            if cancel_event.is_set() or goal.status == GoalStatus.CANCELLED:
                self._transition(goal, GoalStatus.CANCELLED, "goal.cancelled")
                return goal

            if not report.success:
                failed = [o.task_id for o in report.outcomes if o.status == "failed"]
                self._transition(goal, GoalStatus.FAILED, "goal.failed", {"failed_tasks": failed})
                return goal

            approval_tasks = [t for t in goal.plan.tasks if t.approval_required]
            if approval_tasks:
                # An agent may already have requested a publish approval for
                # this goal (e.g. the reviewer on the publish task). Adopt the
                # existing pending approval instead of requesting a duplicate,
                # so exactly one human decision gates publishing.
                existing = [
                    a
                    for a in self.approvals.pending()
                    if a.action == "publish" and a.details.get("goal_id") == goal.id
                ]
                if existing:
                    approval = existing[0]
                    self._audit(
                        "goal.approval_adopted", goal,
                        {"approval_id": approval.id},
                    )
                else:
                    approval = self.approvals.request(
                        "publish",
                        {
                            "goal_id": goal.id,
                            "artifacts": goal.artifacts,
                            "tasks": [t.id for t in approval_tasks],
                        },
                    )
                self._transition(
                    goal, GoalStatus.AWAITING_APPROVAL, "goal.awaiting_approval",
                    {"approval_id": approval.id},
                )
                if auto_approve:
                    approval = self.approvals.resolve(approval.id, True, note="auto-approved")
                else:
                    approval = self.approvals.wait(approval.id)
                if approval.status == "approved":
                    self._transition(goal, GoalStatus.COMPLETED, "goal.completed",
                                     {"approval_id": approval.id})
                else:
                    self._transition(goal, GoalStatus.FAILED, "goal.failed",
                                     {"reason": f"approval {approval.status}", "approval_id": approval.id})
            else:
                self._transition(goal, GoalStatus.COMPLETED, "goal.completed")
            return goal
        except Exception as exc:  # noqa: BLE001 - any failure fails the goal, honestly
            if cancel_event.is_set() or goal.status == GoalStatus.CANCELLED:
                self._transition(goal, GoalStatus.CANCELLED, "goal.cancelled")
            else:
                self._transition(
                    goal, GoalStatus.FAILED, "goal.failed",
                    {"error": f"{type(exc).__name__}: {exc}"},
                )
            return goal
        finally:
            self._cancel_events.pop(goal_id, None)

    def cancel_goal(self, goal_id: str) -> Goal:
        """Cancel a goal: signal the run loop and mark it CANCELLED."""
        goal = self.get_goal(goal_id)
        if goal.status in (GoalStatus.COMPLETED, GoalStatus.FAILED, GoalStatus.CANCELLED):
            raise GoalStateError(f"cannot cancel goal in terminal status {goal.status.value!r}")
        event = self._cancel_events.get(goal_id)
        if event is None:
            event = threading.Event()
            self._cancel_events[goal_id] = event
        event.set()
        self._transition(goal, GoalStatus.CANCELLED, "goal.cancelled")
        return goal

    def resolve_approval(self, approval_id: str, approved: bool, note: str = "") -> Any:
        """Resolve a pending approval; delegates to the approval gate."""
        return self._require("approvals").resolve(approval_id, approved, note=note)
