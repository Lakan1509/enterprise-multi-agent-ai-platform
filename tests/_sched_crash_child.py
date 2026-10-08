"""Child process for the scheduler crash-resume test. NOT a test module.

Runs a two-task plan (a -> b) with a checkpoint file. The parent test kills
this process mid-DAG (while task ``b`` is sleeping) to simulate a crash,
then resumes from the checkpoint in its own process. Task ``a`` appends to
``runs.txt`` so the parent can prove it was not re-executed on resume.
"""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from casi.planner import Plan, TaskSpec
from casi.scheduler import DAGScheduler


def _ok(message: str) -> SimpleNamespace:
    return SimpleNamespace(
        success=True, message=message, error=None, output={}, artifacts=[]
    )


class AgentA:
    """Task a: records that it ran, then succeeds immediately."""

    def __init__(self, counter: str) -> None:
        self._counter = counter

    def run(self, ctx):
        with open(self._counter, "a", encoding="utf-8") as fh:
            fh.write("a\n")
        return _ok("a done")


class AgentB:
    """Task b: sleeps long enough for the parent to kill us mid-DAG."""

    def run(self, ctx):
        time.sleep(120)
        return _ok("b done")


class _Registry:
    def __init__(self, agents):
        self._agents = agents

    def find(self, capability):
        return self._agents[capability]


class _Audit:
    def record(self, *args, **kwargs):
        return None


def main() -> None:
    tmpdir, ckpt = sys.argv[1], sys.argv[2]
    counter = str(Path(tmpdir) / "runs.txt")
    plan = Plan(
        goal_id="g",
        goal_text="t",
        tasks=[
            TaskSpec(id="a", name="a", description="a", agent_capability="a"),
            TaskSpec(
                id="b",
                name="b",
                description="b",
                agent_capability="b",
                depends_on=["a"],
            ),
        ],
    )
    sched = DAGScheduler(
        _Registry({"a": AgentA(counter), "b": AgentB()}), _Audit()
    )
    sched.run(plan, lambda task: None, checkpoint_path=Path(ckpt))


if __name__ == "__main__":
    main()
