"""Tests for the CASI FastAPI surface: auth, RBAC, and goal lifecycle."""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from casi.api.app import create_app
from casi.config import InsecureDeploymentError, Settings
from casi.kernel import AIKernel

GOAL_TEXT = (
    "Write a Python function `sort_list` that sorts a list of numbers "
    "ascending, and include unit tests."
)

# Strong (>= 32 chars) test keys for each role.
ADMIN_KEY = "adminkey-" + "a" * 32
OPERATOR_KEY = "operatorkey-" + "o" * 32
VIEWER_KEY = "viewerkey-" + "v" * 32


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("CASI_API_KEY", ADMIN_KEY)
    monkeypatch.setenv("CASI_OPERATOR_API_KEY", OPERATOR_KEY)
    monkeypatch.setenv("CASI_VIEWER_API_KEY", VIEWER_KEY)
    data_dir = Path(tempfile.mkdtemp(prefix="casi-api-test-"))
    kernel = AIKernel(Settings(data_dir=data_dir))
    app = create_app(kernel)
    # Default to the admin key; tests can override per-request headers.
    with TestClient(app, headers={"X-API-Key": ADMIN_KEY}) as c:
        yield c


def _bare_client(monkeypatch, headers=None):
    """Build a client without default auth headers (caller picks headers)."""
    monkeypatch.setenv("CASI_API_KEY", ADMIN_KEY)
    monkeypatch.setenv("CASI_OPERATOR_API_KEY", OPERATOR_KEY)
    monkeypatch.setenv("CASI_VIEWER_API_KEY", VIEWER_KEY)
    data_dir = Path(tempfile.mkdtemp(prefix="casi-api-bare-"))
    kernel = AIKernel(Settings(data_dir=data_dir))
    app = create_app(kernel)
    return TestClient(app, headers=headers or {}), kernel


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_goal_lifecycle(client):
    r = client.post("/goals", json={"text": GOAL_TEXT})
    assert r.status_code == 200
    goal_id = r.json()["id"]
    assert r.json()["status"] == "created"

    r = client.get(f"/goals/{goal_id}")
    assert r.status_code == 200

    r = client.get("/goals")
    assert r.status_code == 200
    assert any(g["id"] == goal_id for g in r.json())

    r = client.get("/goals/does-not-exist")
    assert r.status_code == 404


def test_plan_before_run_is_409(client):
    r = client.post("/goals", json={"text": GOAL_TEXT})
    goal_id = r.json()["id"]
    r = client.get(f"/goals/{goal_id}/plan")
    assert r.status_code == 409


def test_run_goal_auto_approve_completes_as_admin(client):
    r = client.post("/goals", json={"text": GOAL_TEXT})
    goal_id = r.json()["id"]
    r = client.post(f"/goals/{goal_id}/run", json={"auto_approve": True})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed", body
    assert len(body["artifacts"]) >= 2
    assert len(body["outcomes"]) == 7


def test_run_goal_auto_approve_writes_audit_record(client):
    r = client.post("/goals", json={"text": GOAL_TEXT})
    goal_id = r.json()["id"]
    r = client.post(f"/goals/{goal_id}/run", json={"auto_approve": True})
    assert r.status_code == 200

    r = client.get(f"/logs?goal_id={goal_id}")
    assert r.status_code == 200
    auto = [e for e in r.json() if e["event"] == "approval.auto_approved"]
    assert len(auto) == 1
    assert auto[0]["details"]["action"] == "publish"
    assert auto[0]["details"]["actor_role"] == "ADMIN"
    assert auto[0]["details"]["approval_id"]


def test_run_goal_auto_approve_denied_for_operator(monkeypatch):
    c, _ = _bare_client(monkeypatch)
    headers = {"X-API-Key": OPERATOR_KEY}
    r = c.post("/goals", json={"text": GOAL_TEXT}, headers=headers)
    assert r.status_code == 200
    goal_id = r.json()["id"]
    r = c.post(f"/goals/{goal_id}/run", json={"auto_approve": True}, headers=headers)
    assert r.status_code == 403


def test_plan_endpoint_after_run(client):
    r = client.post("/goals", json={"text": GOAL_TEXT})
    goal_id = r.json()["id"]
    client.post(f"/goals/{goal_id}/run", json={"auto_approve": True})
    r = client.get(f"/goals/{goal_id}/plan")
    assert r.status_code == 200
    assert len(r.json()["tasks"]) == 7


def test_agents_and_logs(client):
    r = client.get("/agents")
    assert r.status_code == 200
    names = {a["name"] for a in r.json()}
    assert {"coder", "tester", "debugger", "reviewer", "researcher", "planner"} <= names

    r = client.post("/goals", json={"text": GOAL_TEXT})
    goal_id = r.json()["id"]
    r = client.get(f"/logs?goal_id={goal_id}")
    assert r.status_code == 200
    events = [e["event"] for e in r.json()]
    assert "goal.created" in events


def test_approval_flow_manual():
    # Build kernel/app/client locally so the worker thread can call
    # kernel.run_goal directly — TestClient is not thread-safe for
    # concurrent requests, so the HTTP client stays on the main thread.
    import os
    import time

    os.environ["CASI_API_KEY"] = ADMIN_KEY
    try:
        data_dir = Path(tempfile.mkdtemp(prefix="casi-api-manual-"))
        kernel = AIKernel(Settings(data_dir=data_dir))
        app = create_app(kernel)
        headers = {"X-API-Key": ADMIN_KEY}
        with TestClient(app, headers=headers) as local_client:
            r = local_client.post("/goals", json={"text": GOAL_TEXT})
            goal_id = r.json()["id"]

            holder: dict = {}

            def _run():
                # Direct kernel call as the human operator (ADMIN).
                from casi.security.permissions import Role

                holder["goal"] = kernel.run_goal(goal_id, auto_approve=False, role=Role.ADMIN)

            t = threading.Thread(target=_run, daemon=True)
            t.start()

            approval_id = None
            for _ in range(200):
                pr = local_client.get("/approvals/pending")
                pending = pr.json()
                mine = [a for a in pending if a["details"].get("goal_id") == goal_id]
                if mine:
                    approval_id = mine[0]["id"]
                    break
                time.sleep(0.25)
            assert approval_id, "expected a pending publish approval"

            r = local_client.post(
                f"/approvals/{approval_id}/resolve",
                json={"approved": True, "note": "test"},
            )
            assert r.status_code == 200
            assert r.json()["status"] == "approved"

            t.join(timeout=60)
            assert not t.is_alive()
            assert holder["goal"].status.value == "completed"
    finally:
        del os.environ["CASI_API_KEY"]


# --- auth enforcement --------------------------------------------------------


def test_bad_key_rejected_401(monkeypatch):
    c, _ = _bare_client(monkeypatch)
    r = c.get("/health")
    assert r.status_code == 200  # health is public
    r = c.get("/goals", headers={"X-API-Key": "wrong-key"})
    assert r.status_code == 401
    r = c.post("/goals", json={"text": GOAL_TEXT})  # no key at all
    assert r.status_code == 401


def test_viewer_can_read_but_not_mutate(monkeypatch):
    c, _ = _bare_client(monkeypatch)
    viewer = {"X-API-Key": VIEWER_KEY}
    admin = {"X-API-Key": ADMIN_KEY}

    r = c.get("/goals", headers=viewer)
    assert r.status_code == 200

    r = c.post("/goals", json={"text": GOAL_TEXT}, headers=viewer)
    assert r.status_code == 403

    # operator and admin can mutate goals
    r = c.post("/goals", json={"text": GOAL_TEXT}, headers={"X-API-Key": OPERATOR_KEY})
    assert r.status_code == 200
    r = c.post("/goals", json={"text": GOAL_TEXT}, headers=admin)
    assert r.status_code == 200


def test_only_admin_can_resolve_approvals(monkeypatch):
    c, kernel = _bare_client(monkeypatch)
    admin = {"X-API-Key": ADMIN_KEY}
    operator = {"X-API-Key": OPERATOR_KEY}

    r = c.post("/goals", json={"text": GOAL_TEXT}, headers=admin)
    goal_id = r.json()["id"]
    approval = kernel.approvals.request("publish", {"goal_id": goal_id})

    r = c.post(f"/approvals/{approval.id}/resolve", json={"approved": True}, headers=operator)
    assert r.status_code == 403
    assert kernel.approvals.get(approval.id).status == "pending"

    r = c.post(f"/approvals/{approval.id}/resolve", json={"approved": True}, headers=admin)
    assert r.status_code == 200
    assert r.json()["status"] == "approved"


# --- deployment validation ---------------------------------------------------


def _app_startup_settings(monkeypatch, **kwargs):
    monkeypatch.delenv("CASI_API_KEY", raising=False)
    monkeypatch.delenv("CASI_OPERATOR_API_KEY", raising=False)
    monkeypatch.delenv("CASI_VIEWER_API_KEY", raising=False)
    monkeypatch.delenv("CASI_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("CASI_HOST", raising=False)
    return Settings(data_dir=Path(tempfile.mkdtemp(prefix="casi-deploy-")), **kwargs)


def test_nonloopback_without_key_refuses_startup(monkeypatch):
    settings = _app_startup_settings(monkeypatch, host="0.0.0.0")
    kernel = AIKernel(settings)
    with pytest.raises(InsecureDeploymentError):
        create_app(kernel)


def test_nonloopback_localhost_name_without_key_refuses_startup(monkeypatch):
    # "example.com" is not loopback -> must refuse without a key.
    settings = _app_startup_settings(monkeypatch, host="example.com")
    kernel = AIKernel(settings)
    with pytest.raises(InsecureDeploymentError):
        create_app(kernel)


def test_require_auth_without_key_refuses_startup(monkeypatch):
    settings = _app_startup_settings(monkeypatch, require_auth=True)
    kernel = AIKernel(settings)
    with pytest.raises(InsecureDeploymentError):
        create_app(kernel)


def test_weak_key_nonloopback_refuses_startup(monkeypatch):
    settings = _app_startup_settings(monkeypatch, host="0.0.0.0")
    monkeypatch.setenv("CASI_API_KEY", "too-short")
    kernel = AIKernel(Settings(data_dir=settings.data_dir, host="0.0.0.0"))
    with pytest.raises(InsecureDeploymentError):
        create_app(kernel)


def test_placeholder_key_nonloopback_refuses_startup(monkeypatch):
    settings = _app_startup_settings(monkeypatch, host="0.0.0.0")
    monkeypatch.setenv("CASI_API_KEY", "changeme" + "x" * 30)  # long but placeholder
    kernel = AIKernel(Settings(data_dir=settings.data_dir, host="0.0.0.0"))
    with pytest.raises(InsecureDeploymentError):
        create_app(kernel)


def test_nonloopback_with_strong_key_starts(monkeypatch):
    settings = _app_startup_settings(monkeypatch, host="0.0.0.0")
    monkeypatch.setenv("CASI_API_KEY", ADMIN_KEY)
    kernel = AIKernel(Settings(data_dir=settings.data_dir, host="0.0.0.0"))
    app = create_app(kernel)
    assert app is not None


def test_loopback_without_key_starts_with_loud_warning(monkeypatch, capsys):
    settings = _app_startup_settings(monkeypatch, host="127.0.0.1")
    kernel = AIKernel(settings)
    app = create_app(kernel)  # must not raise
    assert app is not None
    captured = capsys.readouterr()
    assert "DISABLED" in captured.err
    assert "CASI_API_KEY" in captured.err


def test_loopback_ipv6_and_localhost_names_are_loopback(monkeypatch):
    for host in ("::1", "localhost", "127.0.0.2"):
        settings = _app_startup_settings(monkeypatch, host=host)
        kernel = AIKernel(settings)
        assert create_app(kernel) is not None  # must not raise
