"""Tests for casi.scheduler.DAGScheduler using fake registries/agents."""

import json
import threading
import time
from dataclasses import dataclass, field

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
