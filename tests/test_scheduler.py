"""Tests for casi.scheduler.DAGScheduler using fake registries/agents."""

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from casi.planner import Plan, TaskSpec, decompose_goal
from casi.scheduler import DAGScheduler, PlanError, RunReport


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
    """Agent fake with scripted behavior: fail `fail_times` then succeed."""

    def __init__(self, name="fake", fail_times=0, artifacts=None, delay_s=0.0):
        self.name = name
        self.fail_times = fail_times
        self.artifacts = artifacts or []
        self.delay_s = delay_s
        self.calls = 0

    def run(self, ctx):
        self.calls += 1
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.calls <= self.fail_times:
            return FakeResult(success=False, message="boom", error="boom")
        return FakeResult(success=True, message="done", artifacts=list(self.artifacts))


class FakeRegistry:
    def __init__(self, agents):
        self._agents = dict(agents)

    def find(self, capability):
        try:
            return self._agents[capability]
        except KeyError:
            raise LookupError(f"no agent for {capability!r}")


class FakeAudit:
    def __init__(self):
        self.events = []

    def record(self, event, goal_id="", task_id="", actor="", details=None):
        entry = {"event": event, "goal_id": goal_id, "task_id": task_id, "details": details or {}}
        self.events.append(entry)
        return entry


def _sched(agents, **kw):
    return DAGScheduler(FakeRegistry(agents), FakeAudit(), max_workers=kw.get("max_workers", 4))


def _plan(tasks):
    return Plan(goal_id="g", goal_text="t", tasks=tasks)


def _t(tid, cap="coder", deps=(), **kw):
    return TaskSpec(id=tid, name=tid, description=f"do {tid}", agent_capability=cap,
                    depends_on=list(deps), **kw)


# ---------------------------------------------------------------------------
# Validation & levels
# ---------------------------------------------------------------------------


def test_levels_ordering_diamond():
    plan = _plan([
        _t("a"), _t("b", deps=["a"]), _t("c", deps=["a"]), _t("d", deps=["b", "c"]),
    ])
    sched = _sched({"coder": FakeAgent()})
    sched.validate(plan)
    levels = sched.levels(plan)
    assert [[t.id for t in lvl] for lvl in levels] == [["a"], ["b", "c"], ["d"]]


def test_levels_deterministic_sorted_within_level():
    plan = _plan([_t("z"), _t("m"), _t("a")])
    levels = _sched({"coder": FakeAgent()}).levels(plan)
    assert [t.id for t in levels[0]] == ["a", "m", "z"]


def test_validate_rejects_duplicates():
    plan = _plan([_t("a"), _t("a")])
    with pytest.raises(PlanError, match="duplicate"):
        _sched({"coder": FakeAgent()}).validate(plan)


def test_validate_rejects_unknown_dep():
    plan = _plan([_t("a", deps=["nope"])])
    with pytest.raises(PlanError, match="unknown task"):
        _sched({"coder": FakeAgent()}).validate(plan)


def test_validate_rejects_self_dep():
    plan = _plan([_t("a", deps=["a"])])
    with pytest.raises(PlanError, match="itself"):
        _sched({"coder": FakeAgent()}).validate(plan)


def test_validate_rejects_cycle():
    plan = _plan([_t("a", deps=["b"]), _t("b", deps=["a"])])
    with pytest.raises(PlanError, match="cycle"):
        _sched({"coder": FakeAgent()}).validate(plan)


# ---------------------------------------------------------------------------
# Run semantics
# ---------------------------------------------------------------------------


def test_run_all_ok_parallel_levels():
    agent = FakeAgent()
    plan = _plan([_t("a"), _t("b"), _t("c", deps=["a", "b"])])
    report = _sched({"coder": agent}).run(plan, lambda task: None)
    assert isinstance(report, RunReport)
    assert report.success is True
    assert [o.status for o in report.outcomes] == ["ok", "ok", "ok"]
    assert agent.calls == 3
    assert report.started_at and report.finished_at


def test_skip_on_failed_dep():
    plan = _plan([_t("t1"), _t("t2", deps=["t1"])])
    agent = FakeAgent(fail_times=99)
    report = _sched({"coder": agent}).run(plan, lambda task: None)
    assert report.success is False
    by_id = {o.task_id: o for o in report.outcomes}
    assert by_id["t1"].status == "failed"
    assert by_id["t1"].attempts == 3  # 1 + max_retries(2)
    assert by_id["t2"].status == "skipped"
    assert by_id["t2"].attempts == 0


def test_conditional_task_skipped_when_trigger_ok():
    plan = _plan([
        _t("t3_run_tests"),
        _t("t4_diagnose_repair", cap="debugger", deps=["t3_run_tests"],
           params={"conditional_on_failure_of": "t3_run_tests"}),
        _t("t5_rerun", deps=["t4_diagnose_repair"]),
    ])
    tester = FakeAgent()
    debugger = FakeAgent()
    report = _sched({"tester": tester, "debugger": debugger, "coder": FakeAgent()}).run(
        plan, lambda task: None)
    by_id = {o.task_id: o for o in report.outcomes}
    assert by_id["t3_run_tests"].status == "ok"
    assert by_id["t4_diagnose_repair"].status == "skipped"
    assert debugger.calls == 0
    # t5's dep (t4) was skipped because the trigger did not fire ->
    # the skip counts as satisfied and t5 runs normally.
    assert by_id["t5_rerun"].status == "ok"
    assert report.success


def test_conditional_task_runs_when_trigger_failed():
    plan = _plan([
        _t("t3_run_tests", cap="tester"),
        _t("t4_diagnose_repair", cap="debugger", deps=["t3_run_tests"],
           params={"conditional_on_failure_of": "t3_run_tests"}),
    ])
    tester = FakeAgent(fail_times=99)
    debugger = FakeAgent()
    report = _sched({"tester": tester, "debugger": debugger}).run(plan, lambda task: None)
    by_id = {o.task_id: o for o in report.outcomes}
    assert by_id["t3_run_tests"].status == "failed"
    assert by_id["t4_diagnose_repair"].status == "ok"
    assert debugger.calls == 1


def test_retry_then_success():
    agent = FakeAgent(fail_times=1)
    report = _sched({"coder": agent}).run(_plan([_t("a")]), lambda task: None)
    by_id = {o.task_id: report.outcome_for(o.task_id) for o in report.outcomes}
    assert by_id["a"].status == "ok"
    assert by_id["a"].attempts == 2
    assert agent.calls == 2


def test_agent_exception_becomes_failed_outcome():
    class Exploding:
        def run(self, ctx):
            raise RuntimeError("kaput")

    report = _sched({"coder": Exploding()}).run(_plan([_t("a")]), lambda task: None)
    assert report.outcomes[0].status == "failed"
    assert "kaput" in (report.outcomes[0].error or "")


def test_missing_capability_becomes_failed_outcome():
    report = _sched({}).run(_plan([_t("a", cap="wizard")]), lambda task: None)
    assert report.outcomes[0].status == "failed"
    assert "wizard" in (report.outcomes[0].error or "")


def test_checkpoint_resume_skips_completed(tmp_path):
    ckpt = tmp_path / "checkpoint.json"
    plan = _plan([_t("a"), _t("b", deps=["a"])])

    # Simulate a previous run that finished "a" but not "b".
    ckpt.write_text(json.dumps({
        "goal_id": "g",
        "task_ids": ["a", "b"],
        "outcomes": [{"task_id": "a", "status": "ok", "attempts": 1, "error": None}],
    }))

    agent = FakeAgent()
    report = _sched({"coder": agent}).run(plan, lambda task: None, checkpoint_path=ckpt)
    assert report.success
    assert agent.calls == 1  # only "b" ran; "a" resumed from checkpoint
    by_id = {o.task_id: o for o in report.outcomes}
    assert by_id["a"].status == "ok" and by_id["a"].attempts == 1
    assert by_id["b"].status == "ok"

    # A fresh full run afterwards writes a complete checkpoint.
    payload = json.loads(ckpt.read_text())
    assert payload["task_ids"] == ["a", "b"]
    assert {o["task_id"]: o["status"] for o in payload["outcomes"]} == {"a": "ok", "b": "ok"}


def test_checkpoint_mismatch_starts_over(tmp_path):
    ckpt = tmp_path / "checkpoint.json"
    ckpt.write_text(json.dumps({"task_ids": ["zzz"], "outcomes": []}))
    agent = FakeAgent()
    report = _sched({"coder": agent}).run(_plan([_t("a")]), lambda task: None, checkpoint_path=ckpt)
    assert report.success
    assert agent.calls == 1


def test_partial_failure_dependents_skip_independents_continue():
    # Diamond: a fails -> b (dep a) skips; c (independent) still runs ok;
    # d (deps b, c) skips because b never went ok.
    plan = _plan([
        _t("a", cap="a"),
        _t("b", cap="b", deps=["a"]),
        _t("c", cap="c"),
        _t("d", cap="d", deps=["b", "c"]),
    ])
    agents = {
        "a": FakeAgent(fail_times=99),
        "b": FakeAgent(),
        "c": FakeAgent(),
        "d": FakeAgent(),
    }
    report = _sched(agents).run(plan, lambda task: None)
    by_id = {o.task_id: o for o in report.outcomes}
    assert by_id["a"].status == "failed"
    assert by_id["b"].status == "skipped"
    assert by_id["b"].attempts == 0
    assert by_id["c"].status == "ok"
    assert by_id["d"].status == "skipped"
    assert agents["b"].calls == 0
    assert agents["c"].calls == 1
    assert agents["d"].calls == 0
    assert report.success is False


def test_same_level_tasks_unaffected_by_sibling_failure():
    # A failure does not abort the rest of its level: y runs even though
    # x (same level) failed.
    plan = _plan([_t("x", cap="x"), _t("y", cap="y")])
    agents = {"x": FakeAgent(fail_times=99), "y": FakeAgent()}
    report = _sched(agents).run(plan, lambda task: None)
    by_id = {o.task_id: o for o in report.outcomes}
    assert by_id["x"].status == "failed"
    assert by_id["y"].status == "ok"
    assert report.success is False


def test_crash_mid_dag_resumes_without_rerunning_completed(tmp_path):
    """A run killed mid-DAG resumes from the checkpoint: completed tasks are
    not re-executed. The child process is SIGKILLed while task b runs; the
    parent then resumes from the child's checkpoint file."""
    child = Path(__file__).parent / "_sched_crash_child.py"
    ckpt = tmp_path / "checkpoint.json"
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).parent.parent))
    proc = subprocess.Popen(
        [sys.executable, str(child), str(tmp_path), str(ckpt)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"child exited early with code {proc.returncode}")
            if ckpt.exists():
                try:
                    payload = json.loads(ckpt.read_text())
                except ValueError:
                    payload = {}
                statuses = {
                    o["task_id"]: o["status"]
                    for o in payload.get("outcomes", [])
                }
                if statuses.get("a") == "ok":
                    break  # checkpointed after level 1; task b now running
            time.sleep(0.2)
        else:
            pytest.fail("child never checkpointed task a")
        # Interrupt mid-DAG: task b is still running in the child.
        proc.kill()
        proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=15)

    runs = (tmp_path / "runs.txt").read_text().split()
    assert runs == ["a"], runs  # a ran once; b never finished

    # Resume in a fresh scheduler: task a must NOT re-run.
    agent_a = FakeAgent()
    agent_b = FakeAgent()
    plan = _plan([_t("a", cap="a"), _t("b", cap="b", deps=["a"])])
    sched = DAGScheduler(
        FakeRegistry({"a": agent_a, "b": agent_b}), FakeAudit()
    )
    report = sched.run(plan, lambda task: None, checkpoint_path=ckpt)
    assert report.success is True
    assert agent_a.calls == 0, "completed task a was re-executed on resume"
    assert agent_b.calls == 1
    by_id = {o.task_id: o for o in report.outcomes}
    assert by_id["a"].status == "ok"
    assert by_id["b"].status == "ok"
    # runs.txt untouched: a truly ran exactly once across both processes
    assert (tmp_path / "runs.txt").read_text().split() == ["a"]


def test_cancellation_marks_remaining_cancelled():
    event = threading.Event()

    class CancellingAgent(FakeAgent):
        def run(self, ctx):
            event.set()
            return super().run(ctx)

    plan = _plan([_t("a"), _t("b", deps=["a"]), _t("c", deps=["b"])])
    report = _sched({"coder": CancellingAgent()}).run(plan, lambda task: None, cancel_event=event)
    by_id = {o.task_id: o for o in report.outcomes}
    assert by_id["a"].status == "ok"
    assert by_id["b"].status == "cancelled"
    assert by_id["c"].status == "cancelled"
    assert report.success is False


def test_sort_goal_plan_runs_end_to_end():
    plan = decompose_goal(
        "Write a Python function `sort_list` that sorts a list of numbers ascending, and include unit tests.",
        "g-sort",
    )
    agents = {
        "code": FakeAgent(artifacts=["sort_list.py"]),
        "test": FakeAgent(),
        "debug": FakeAgent(),
        "review": FakeAgent(),
    }
    report = _sched(agents).run(plan, lambda task: None)
    by_id = {o.task_id: o for o in report.outcomes}
    # t3 passes in the fake world, so t4 (repair) is skipped as not triggered;
    # the skip counts as satisfied for t5/t6/t7 and the run succeeds.
    assert by_id["t4_diagnose_repair"].status == "skipped"
    assert report.success is True
    assert by_id["t1_write_impl"].status == "ok"
