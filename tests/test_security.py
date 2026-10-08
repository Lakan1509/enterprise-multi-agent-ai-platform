"""Tests for casi.security: permissions, approvals, audit log, auth."""

import inspect
import threading
import time

import pytest

from casi.config import Settings
from casi.kernel import AIKernel
from casi.security import (
    ApprovalGate,
    ApprovalNotFound,
    ApprovalTimeout,
    AuditLog,
    Capability,
    PermissionDenied,
    Role,
    check,
    verify_api_key,
)


# --- permissions -----------------------------------------------------------


def test_viewer_denied_execute_code() -> None:
    with pytest.raises(PermissionDenied):
        check(Role.VIEWER, Capability.EXECUTE_CODE)


def test_viewer_allowed_read_workspace() -> None:
    check(Role.VIEWER, Capability.READ_WORKSPACE)  # must not raise


def test_operator_allowed_code_and_workspace() -> None:
    check(Role.OPERATOR, Capability.EXECUTE_CODE)
    check(Role.OPERATOR, Capability.READ_WORKSPACE)
    check(Role.OPERATOR, Capability.WRITE_WORKSPACE)
    check(Role.OPERATOR, Capability.NETWORK)


def test_operator_denied_publish_and_plugins() -> None:
    with pytest.raises(PermissionDenied):
        check(Role.OPERATOR, Capability.APPROVE_PUBLISH)
    with pytest.raises(PermissionDenied):
        check(Role.OPERATOR, Capability.MANAGE_PLUGINS)


def test_approve_own_runs_admin_only() -> None:
    """APPROVE_OWN_RUNS is granted ONLY to ADMIN (not operator, not viewer)."""
    check(Role.ADMIN, Capability.APPROVE_OWN_RUNS)  # must not raise
    with pytest.raises(PermissionDenied):
        check(Role.OPERATOR, Capability.APPROVE_OWN_RUNS)
    with pytest.raises(PermissionDenied):
        check(Role.VIEWER, Capability.APPROVE_OWN_RUNS)


def test_manage_goals_admin_and_operator_only() -> None:
    check(Role.ADMIN, Capability.MANAGE_GOALS)
    check(Role.OPERATOR, Capability.MANAGE_GOALS)
    with pytest.raises(PermissionDenied):
        check(Role.VIEWER, Capability.MANAGE_GOALS)


def test_admin_has_all_capabilities() -> None:
    for capability in Capability:
        check(Role.ADMIN, capability)  # must not raise


def test_unknown_role_denied() -> None:
    with pytest.raises(PermissionDenied):
        check("nobody", Capability.READ_WORKSPACE)


# --- approvals -------------------------------------------------------------


def test_approval_request_resolve_cycle() -> None:
    gate = ApprovalGate()
    approval = gate.request("publish", {"file": "sort_list.py"})
    assert approval.status == "pending"
    assert len(approval.id) == 8
    assert approval.created_at
    assert gate.pending() == [approval]

    resolved = gate.resolve(approval.id, True, note="looks good")
    assert resolved.status == "approved"
    assert resolved.note == "looks good"
    assert resolved.resolved_at
    assert gate.pending() == []
    assert gate.get(approval.id).status == "approved"


def test_approval_reject() -> None:
    gate = ApprovalGate()
    approval = gate.request("publish", {})
    gate.resolve(approval.id, False, note="not yet")
    assert gate.get(approval.id).status == "rejected"


def test_approval_resolve_unknown_raises() -> None:
    gate = ApprovalGate()
    with pytest.raises(ApprovalNotFound):
        gate.resolve("deadbeef", True)
    with pytest.raises(ApprovalNotFound):
        gate.get("deadbeef")


def test_approval_double_resolve_raises() -> None:
    gate = ApprovalGate()
    approval = gate.request("publish", {})
    gate.resolve(approval.id, True)
    with pytest.raises(ValueError, match="already resolved"):
        gate.resolve(approval.id, False)


def test_approval_wait_returns_after_resolve_from_thread() -> None:
    gate = ApprovalGate()
    approval = gate.request("deploy", {})

    def _approve_later() -> None:
        time.sleep(0.1)
        gate.resolve(approval.id, True)

    thread = threading.Thread(target=_approve_later)
    thread.start()
    result = gate.wait(approval.id, timeout_s=5, poll_s=0.05)
    thread.join()
    assert result.status == "approved"


def test_approval_wait_timeout_raises() -> None:
    gate = ApprovalGate()
    approval = gate.request("deploy", {})
    with pytest.raises(ApprovalTimeout):
        gate.wait(approval.id, timeout_s=0.2, poll_s=0.05)


def test_approval_wait_rejected_raises_permission_denied() -> None:
    gate = ApprovalGate()
    approval = gate.request("deploy", {})
    gate.resolve(approval.id, False)
    with pytest.raises(PermissionDenied, match="rejected"):
        gate.wait(approval.id)


def test_approval_gate_audit_hook_receives_events() -> None:
    events: list = []
    gate = ApprovalGate(audit_hook=lambda event, details: events.append((event, details)))
    approval = gate.request("publish", {"goal_id": "g1"})
    gate.resolve(approval.id, True, note="ok")

    names = [e for e, _ in events]
    assert names == ["approval.requested", "approval.resolved"]
    requested = events[0][1]
    assert requested["approval_id"] == approval.id
    assert requested["action"] == "publish"
    assert requested["goal_id"] == "g1"
    resolved = events[1][1]
    assert resolved["status"] == "approved"
    assert resolved["note"] == "ok"


def test_approval_gate_audit_hook_failure_does_not_break_gate() -> None:
    def _boom(event: str, details: dict) -> None:
        raise RuntimeError("audit sink down")

    gate = ApprovalGate(audit_hook=_boom)
    approval = gate.request("publish", {})
    resolved = gate.resolve(approval.id, True)
    assert resolved.status == "approved"  # gate still works


def test_approval_gate_without_hook_still_works() -> None:
    gate = ApprovalGate()
    approval = gate.request("publish", {})
    assert gate.resolve(approval.id, True).status == "approved"


# --- audit -----------------------------------------------------------------


def test_audit_record_and_read(tmp_path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    entry = log.record("goal.created", goal_id="g1", actor="api", details={"x": 1})
    assert entry["event"] == "goal.created"
    assert entry["goal_id"] == "g1"
    assert entry["actor"] == "api"
    assert entry["details"] == {"x": 1}
    assert entry["ts"]

    log.record("goal.created", goal_id="g2")
    log.record("task.done", goal_id="g1", task_id="t1")

    entries = log.read()
    assert len(entries) == 3
    # newest last == file order
    assert [e["event"] for e in entries] == ["goal.created", "goal.created", "task.done"]

    g1 = log.read(goal_id="g1")
    assert len(g1) == 2
    assert all(e["goal_id"] == "g1" for e in g1)

    assert len(log.read(limit=1)) == 1
    assert log.read(goal_id="nope") == []


def test_audit_read_missing_file(tmp_path) -> None:
    assert AuditLog(tmp_path / "missing.jsonl").read() == []


# --- auth ------------------------------------------------------------------


class _Request:
    def __init__(self, headers: dict) -> None:
        self.headers = headers


def test_verify_api_key_viewer_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("CASI_API_KEY", raising=False)
    monkeypatch.delenv("CASI_OPERATOR_API_KEY", raising=False)
    monkeypatch.delenv("CASI_VIEWER_API_KEY", raising=False)
    assert verify_api_key(_Request({})) is Role.VIEWER


def test_verify_api_key_admin_when_main_key_matches(monkeypatch) -> None:
    monkeypatch.setenv("CASI_API_KEY", "admin-secret-123")
    assert verify_api_key(_Request({"X-API-Key": "admin-secret-123"})) is Role.ADMIN


def test_verify_api_key_role_mapping(monkeypatch) -> None:
    """Documented key -> role mapping: admin / operator / viewer keys."""
    monkeypatch.setenv("CASI_API_KEY", "adminkey")
    monkeypatch.setenv("CASI_OPERATOR_API_KEY", "operatorkey")
    monkeypatch.setenv("CASI_VIEWER_API_KEY", "viewerkey")
    assert verify_api_key(_Request({"X-API-Key": "adminkey"})) is Role.ADMIN
    assert verify_api_key(_Request({"X-API-Key": "operatorkey"})) is Role.OPERATOR
    assert verify_api_key(_Request({"X-API-Key": "viewerkey"})) is Role.VIEWER


def test_verify_api_key_401_on_mismatch(monkeypatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi import HTTPException

    monkeypatch.setenv("CASI_API_KEY", "secret-123")
    with pytest.raises(HTTPException) as exc_info:
        verify_api_key(_Request({"X-API-Key": "wrong"}))
    assert exc_info.value.status_code == 401
    with pytest.raises(HTTPException):
        verify_api_key(_Request({}))


def test_verify_api_key_unknown_key_401_when_any_key_set(monkeypatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi import HTTPException

    # Only a viewer key is configured: the admin key is *not* implicitly set.
    monkeypatch.delenv("CASI_API_KEY", raising=False)
    monkeypatch.delenv("CASI_OPERATOR_API_KEY", raising=False)
    monkeypatch.setenv("CASI_VIEWER_API_KEY", "viewerkey")
    assert verify_api_key(_Request({"X-API-Key": "viewerkey"})) is Role.VIEWER
    with pytest.raises(HTTPException) as exc_info:
        verify_api_key(_Request({"X-API-Key": "adminkey"}))
    assert exc_info.value.status_code == 401


# --- approval-gate integrity (kernel auto-approve path) ----------------------


def _kernel_with_audit(tmp_path):
    """Real kernel + real audit log in an isolated data dir."""
    kernel = AIKernel(Settings(data_dir=tmp_path / "kdata"))
    return kernel


def test_auto_approve_without_capability_raises_and_audits(tmp_path) -> None:
    """Bypass attempt without APPROVE_OWN_RUNS fails closed + is audited."""
    kernel = _kernel_with_audit(tmp_path)
    goal = kernel.create_goal("do the thing")
    with pytest.raises(PermissionDenied):
        kernel.run_goal(goal.id, auto_approve=True, role=Role.OPERATOR)
    events = kernel.audit.read(goal_id=goal.id)
    denied = [e for e in events if e["event"] == "approval.bypass_denied"]
    assert len(denied) == 1
    assert denied[0]["actor"] == "OPERATOR"
    # the goal itself was never started — fail fast, before any work
    assert "goal.planned" not in [e["event"] for e in events]


def test_auto_approve_with_viewer_role_denied(tmp_path) -> None:
    kernel = _kernel_with_audit(tmp_path)
    goal = kernel.create_goal("do the thing")
    with pytest.raises(PermissionDenied):
        kernel.run_goal(goal.id, auto_approve=True, role=Role.VIEWER)
    events = kernel.audit.read(goal_id=goal.id)
    assert any(e["event"] == "approval.bypass_denied" for e in events)


def test_auto_approve_without_role_denied(tmp_path) -> None:
    """No acting role at all -> fail closed, never a silent bypass."""
    kernel = _kernel_with_audit(tmp_path)
    goal = kernel.create_goal("do the thing")
    with pytest.raises(PermissionDenied):
        kernel.run_goal(goal.id, auto_approve=True)
    events = kernel.audit.read(goal_id=goal.id)
    assert any(e["event"] == "approval.bypass_denied" for e in events)


def test_auto_approve_with_admin_writes_audit_record(tmp_path) -> None:
    """An exercised auto-approve writes approval.auto_approved with id/action/actor."""
    kernel = _kernel_with_audit(tmp_path)
    goal = kernel.create_goal("do the thing")
    approval = kernel.approvals.request("publish", {"goal_id": goal.id})
    resolved = kernel._auto_resolve_approval(goal, approval, Role.ADMIN)
    assert resolved.status == "approved"

    events = kernel.audit.read(goal_id=goal.id)
    auto = [e for e in events if e["event"] == "approval.auto_approved"]
    assert len(auto) == 1
    details = auto[0]["details"]
    assert details["approval_id"] == approval.id
    assert details["action"] == "publish"
    assert details["actor_role"] == "ADMIN"


def test_auto_resolve_without_capability_denied_and_audited(tmp_path) -> None:
    kernel = _kernel_with_audit(tmp_path)
    goal = kernel.create_goal("do the thing")
    approval = kernel.approvals.request("publish", {"goal_id": goal.id})
    with pytest.raises(PermissionDenied):
        kernel._auto_resolve_approval(goal, approval, Role.OPERATOR)
    events = kernel.audit.read(goal_id=goal.id)
    assert any(e["event"] == "approval.bypass_denied" for e in events)
    # approval left untouched — still pending
    assert kernel.approvals.get(approval.id).status == "pending"


def test_auto_approve_is_never_a_config_default() -> None:
    """No Settings knob can silently enable auto-approve; the kernel default is False."""
    assert not hasattr(Settings(), "auto_approve")
    sig = inspect.signature(AIKernel.run_goal)
    assert sig.parameters["auto_approve"].default is False


def test_kernel_wires_audit_hook_into_gate(tmp_path) -> None:
    """The kernel's ApprovalGate feeds approval events into the audit log."""
    kernel = _kernel_with_audit(tmp_path)
    goal = kernel.create_goal("do the thing")
    approval = kernel.approvals.request("publish", {"goal_id": goal.id})
    kernel.approvals.resolve(approval.id, True, note="human")

    events = kernel.audit.read(goal_id=goal.id)
    names = [e["event"] for e in events]
    assert "approval.requested" in names
    assert "approval.resolved" in names
