"""Tests for casi.security: permissions, approvals, audit log, auth."""

import threading
import time

import pytest

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
    assert verify_api_key(_Request({})) is Role.VIEWER


def test_verify_api_key_operator_when_match(monkeypatch) -> None:
    monkeypatch.setenv("CASI_API_KEY", "secret-123")
    assert verify_api_key(_Request({"X-API-Key": "secret-123"})) is Role.OPERATOR


def test_verify_api_key_401_on_mismatch(monkeypatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi import HTTPException

    monkeypatch.setenv("CASI_API_KEY", "secret-123")
    with pytest.raises(HTTPException) as exc_info:
        verify_api_key(_Request({"X-API-Key": "wrong"}))
    assert exc_info.value.status_code == 401
    with pytest.raises(HTTPException):
        verify_api_key(_Request({}))
