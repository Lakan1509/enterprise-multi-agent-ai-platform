"""Tests for the CASI FastAPI surface."""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from casi.api.app import create_app
from casi.config import Settings
from casi.kernel import AIKernel

GOAL_TEXT = (
    "Write a Python function `sort_list` that sorts a list of numbers "
    "ascending, and include unit tests."
)


@pytest.fixture()
def client():
    data_dir = Path(tempfile.mkdtemp(prefix="casi-api-test-"))
    kernel = AIKernel(Settings(data_dir=data_dir))
    app = create_app(kernel)
    with TestClient(app) as c:
        yield c


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


def test_run_goal_auto_approve_completes(client):
    r = client.post("/goals", json={"text": GOAL_TEXT})
    goal_id = r.json()["id"]
    r = client.post(f"/goals/{goal_id}/run", json={"auto_approve": True})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "completed", body
    assert len(body["artifacts"]) >= 2
    assert len(body["outcomes"]) == 7


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
    import time

    data_dir = Path(tempfile.mkdtemp(prefix="casi-api-manual-"))
    kernel = AIKernel(Settings(data_dir=data_dir))
    app = create_app(kernel)
    with TestClient(app) as local_client:
        r = local_client.post("/goals", json={"text": GOAL_TEXT})
        goal_id = r.json()["id"]

        holder: dict = {}

        def _run():
            holder["goal"] = kernel.run_goal(goal_id, auto_approve=False)

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
