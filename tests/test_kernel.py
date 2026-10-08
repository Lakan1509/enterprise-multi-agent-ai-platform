"""Tests for casi.kernel.AIKernel using stub/fake collaborators."""

import threading
import time
import uuid
from dataclasses import dataclass, field

import pytest

from casi.config import Settings
from casi.kernel import AIKernel, GoalNotFound, GoalStateError, GoalStatus
from casi.scheduler import DAGScheduler
from casi.security.permissions import Role


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeResult:
    success: bool
    output: dict = field(default_factory=dict)
    artifacts: list = field(default_factory=list)
    message: str = ""
    error: str | None = None


class FakeAgent:
    def __init__(self, fail=False, artifacts=None, delay_s=0.0):
        self.fail = fail
        self.artifacts = artifacts or []
        self.delay_s = delay_s
        self.calls = 0

    def run(self, ctx):
        self.calls += 1
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.fail:
            return FakeResult(success=False, message="nope", error="nope")
        return FakeResult(success=True, message="done", artifacts=list(self.artifacts))


class FakeRegistry:
    def __init__(self, agent):
        self._agent = agent

    def find(self, capability):
        return self._agent


@dataclass
class FakeApproval:
    id: str
    action: str
    details: dict
    status: str = "pending"
    created_at: str = ""
    resolved_at: str | None = None
    note: str = ""


class FakeApprovalGate:
    """wait() returns the still-pending approval (simulates no human decision)."""

    def __init__(self):
        self.approvals = {}

    def request(self, action, details):
        ap = FakeApproval(id=uuid.uuid4().hex[:8], action=action, details=details)
        self.approvals[ap.id] = ap
        return ap

    def resolve(self, approval_id, approved, note=""):
        ap = self.approvals[approval_id]
        ap.status = "approved" if approved else "rejected"
        ap.note = note
        return ap

    def get(self, approval_id):
        return self.approvals[approval_id]

    def pending(self):
        return [a for a in self.approvals.values() if a.status == "pending"]

    def wait(self, approval_id, timeout_s=3600, poll_s=1.0):
        return self.approvals[approval_id]  # still pending -> kernel treats as not approved


class FakeAudit:
    def __init__(self):
        self.events = []

    def record(self, event, goal_id="", task_id="", actor="", details=None):
        self.events.append({"event": event, "goal_id": goal_id, "task_id": task_id,
                            "details": details or {}})
        return self.events[-1]

    def event_names(self):
        return [e["event"] for e in self.events]


class FakeWorkspace:
    def __init__(self, root):
        self.root = root


def make_kernel(tmp_path, agent=None, approvals=None):
    """Kernel with all lazy components replaced by fakes."""
    settings = Settings(data_dir=tmp_path / "data")
    kernel = AIKernel(settings)
    agent = agent or FakeAgent(artifacts=["sort_list.py"])
    approvals = approvals or FakeApprovalGate()
    audit = FakeAudit()
    kernel.registry = FakeRegistry(agent)
    kernel.scheduler = DAGScheduler(kernel.registry, audit, max_workers=2)
    kernel.approvals = approvals
    kernel.audit = audit
    kernel.workspace = FakeWorkspace(settings.resolved_workspace_dir)
    return kernel, agent, approvals, audit


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_create_goal_validates_and_audits(tmp_path):
    kernel, _, _, audit = make_kernel(tmp_path)
    with pytest.raises(ValueError):
        kernel.create_goal("   ")
    goal = kernel.create_goal("Write a Python function `sort_list` that sorts a list of numbers ascending, and include unit tests.")
    assert goal.status == GoalStatus.CREATED
    assert len(goal.id) == 12
    assert "goal.created" in audit.event_names()
    assert kernel.get_goal(goal.id) is goal
    assert kernel.list_goals() == [goal]


def test_get_goal_unknown_raises(tmp_path):
    kernel, _, _, _ = make_kernel(tmp_path)
    with pytest.raises(GoalNotFound):
        kernel.get_goal("nope")


def test_run_goal_auto_approve_completes(tmp_path):
    kernel, agent, approvals, audit = make_kernel(tmp_path)
    goal = kernel.create_goal("do something vague and non-software")
    result = kernel.run_goal(goal.id, auto_approve=True, role=Role.ADMIN)
    assert result.status == GoalStatus.COMPLETED
    assert result.plan is not None
    assert result.report is not None and result.report.success
    assert result.artifacts == ["sort_list.py"]
    names = audit.event_names()
    for expected in ("goal.created", "goal.planned", "goal.running",
                     "goal.awaiting_approval", "goal.completed"):
        assert expected in names
    # auto-approve resolved the publish approval
    assert approvals.pending() == []


def test_run_goal_approval_gate_blocks_without_auto_approve(tmp_path):
    kernel, agent, approvals, audit = make_kernel(tmp_path)
    goal = kernel.create_goal("do something vague and non-software")
    result = kernel.run_goal(goal.id, auto_approve=False)
    # Fake gate never approves -> kernel must NOT complete
    assert result.status == GoalStatus.FAILED
    assert "goal.awaiting_approval" in audit.event_names()
    assert len(approvals.pending()) == 1
    assert approvals.pending()[0].action == "publish"


def test_run_goal_failed_task_marks_failed(tmp_path):
    kernel, agent, approvals, audit = make_kernel(tmp_path, agent=FakeAgent(fail=True))
    goal = kernel.create_goal("do something vague and non-software")
    result = kernel.run_goal(goal.id, auto_approve=True, role=Role.ADMIN)
    assert result.status == GoalStatus.FAILED
    assert result.report is not None and not result.report.success
    assert "goal.failed" in audit.event_names()
    assert approvals.pending() == []  # gate never reached


def test_run_goal_requires_runnable_status(tmp_path):
    kernel, _, _, _ = make_kernel(tmp_path)
    goal = kernel.create_goal("do something vague and non-software")
    kernel.run_goal(goal.id, auto_approve=True, role=Role.ADMIN)
    with pytest.raises(GoalStateError):
        kernel.run_goal(goal.id, auto_approve=True, role=Role.ADMIN)


def test_cancel_goal_during_run(tmp_path):
    slow = FakeAgent(delay_s=1.5)
    kernel, _, _, audit = make_kernel(tmp_path, agent=slow)
    goal = kernel.create_goal("do something vague and non-software")

    thread = threading.Thread(target=kernel.run_goal, args=(goal.id,), kwargs={"auto_approve": True, "role": Role.ADMIN})
    thread.start()
    time.sleep(0.3)
    kernel.cancel_goal(goal.id)
    thread.join(timeout=15)

    assert kernel.get_goal(goal.id).status == GoalStatus.CANCELLED
    assert "goal.cancelled" in audit.event_names()


def test_cancel_terminal_goal_raises(tmp_path):
    kernel, _, _, _ = make_kernel(tmp_path)
    goal = kernel.create_goal("do something vague and non-software")
    kernel.run_goal(goal.id, auto_approve=True, role=Role.ADMIN)
    with pytest.raises(GoalStateError):
        kernel.cancel_goal(goal.id)


def test_resolve_approval_delegates(tmp_path):
    kernel, _, approvals, _ = make_kernel(tmp_path)
    ap = approvals.request("publish", {"goal_id": "g"})
    resolved = kernel.resolve_approval(ap.id, True, note="lgtm")
    assert resolved.status == "approved"
    assert resolved.note == "lgtm"


def test_kernel_importable_without_other_builders(tmp_path):
    # Constructor must not blow up when sibling modules are absent.
    kernel = AIKernel(Settings(data_dir=tmp_path / "d2"))
    assert kernel.scheduler is not None
    assert kernel.settings is not None


def test_ctx_factory_carries_role(tmp_path):
    """Kernel wires the run's role onto every AgentContext (D-stream follow-up)."""
    from casi.planner import TaskSpec

    kernel, _, _, _ = make_kernel(tmp_path)
    goal = kernel.create_goal("do something vague and non-software")
    task = TaskSpec(id="t1", name="probe", description="probe",
                    agent_capability="coder")
    ctx = kernel._make_ctx_factory(goal, Role.OPERATOR)(task)
    assert ctx.role == Role.OPERATOR


def test_ctx_factory_defaults_to_ungated_legacy(tmp_path):
    """No role passed -> role=None (legacy ungated contexts, documented)."""
    from casi.planner import TaskSpec

    kernel, _, _, _ = make_kernel(tmp_path)
    goal = kernel.create_goal("do something vague and non-software")
    task = TaskSpec(id="t1", name="probe", description="probe",
                    agent_capability="coder")
    ctx = kernel._make_ctx_factory(goal)(task)
    assert ctx.role is None


def test_run_goal_propagates_role_to_agents(tmp_path):
    """Agents observe the run's role end-to-end via run_goal(role=...)."""
    seen_roles = []

    class RoleCapturingAgent(FakeAgent):
        def run(self, ctx):
            seen_roles.append(ctx.role)
            return super().run(ctx)

    kernel, _, _, _ = make_kernel(tmp_path, agent=RoleCapturingAgent(artifacts=["sort_list.py"]))
    goal = kernel.create_goal("do something vague and non-software")
    result = kernel.run_goal(goal.id, auto_approve=True, role=Role.ADMIN)
    assert result.status == GoalStatus.COMPLETED
    assert seen_roles, "no agent ran"
    assert all(r == Role.ADMIN for r in seen_roles)
