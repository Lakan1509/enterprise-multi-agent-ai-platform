"""Rule-based goal decomposition.

``decompose_goal`` deterministically turns a free-text goal into a
``Plan`` of ``TaskSpec`` tasks forming a DAG. It is deliberately
keyword/pattern driven — genuine, inspectable logic — not random. The
``model_router`` parameter is a documented hook for future LLM-assisted
decomposition; it is currently unused.

Vague goals (e.g. ``"make it better"``) never produce a garbage DAG: they
yield a single-task *clarifying plan* whose task carries
``params["needs_clarification"]``. The supervisor agent short-circuits such
tasks — no routing, no side effects — and returns the clarifying questions
so the caller can ask the user. Empty/blank goals are refused outright with
``ValueError``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskSpec:
    """A single unit of work in a plan.

    Attributes:
        id: Stable, unique slug identifying the task within the plan.
        name: Short human-readable name.
        description: What the task must accomplish.
        agent_capability: Capability key used for agent assignment, e.g.
            ``"coder"``, ``"tester"``, ``"debugger"``, ``"reviewer"``,
            ``"researcher"``, ``"plan"``.
        depends_on: Ids of tasks that must finish ``ok`` before this one runs.
        max_retries: Times the scheduler retries a failing task.
        timeout_s: Per-attempt wall-clock budget for the task.
        approval_required: Whether the kernel must hold for human approval
            after this task completes.
        params: Arbitrary task parameters consumed by agents/scheduler.
    """

    id: str
    name: str
    description: str
    agent_capability: str
    depends_on: list[str] = field(default_factory=list)
    max_retries: int = 2
    timeout_s: int = 120
    approval_required: bool = False
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Plan:
    """An ordered collection of tasks forming a DAG."""

    goal_id: str
    goal_text: str
    tasks: list[TaskSpec]

    def task_ids(self) -> list[str]:
        """Ids of all tasks in the plan, in plan order."""
        return [t.id for t in self.tasks]


# ---------------------------------------------------------------------------
# Pattern matching helpers
# ---------------------------------------------------------------------------

_FUNC_NAME_RE = re.compile(r"function\s+[`'\"]?([A-Za-z_][A-Za-z0-9_]*)[`'\"]?", re.IGNORECASE)
_CLASS_NAME_RE = re.compile(r"class\s+[`'\"]?([A-Za-z_][A-Za-z0-9_]*)[`'\"]?", re.IGNORECASE)
_FILE_HINT_RE = re.compile(r"[`'\"]?([\w\-/]+\.py)[`'\"]?")


def _extract_target_name(goal_text: str) -> str | None:
    """Best-effort extraction of a code symbol named in the goal text."""
    m = _FUNC_NAME_RE.search(goal_text) or _CLASS_NAME_RE.search(goal_text)
    return m.group(1) if m else None


def _is_software_goal(goal_text: str) -> bool:
    """Heuristic: the goal asks for code to be written."""
    lowered = goal_text.lower()
    triggers = (
        "write a python",
        "write python",
        "write a function",
        "write function",
        "implement",
        "create a function",
        "code a",
        "script",
    )
    return any(t in lowered for t in triggers) or bool(_FUNC_NAME_RE.search(goal_text))


# ---------------------------------------------------------------------------
# Vague-goal handling
# ---------------------------------------------------------------------------

#: Phrases that carry no actionable target on their own.
_VAGUE_PATTERNS = (
    re.compile(r"\bmake it better\b"),
    re.compile(r"\bimprove (it|this|that)\b"),
    re.compile(r"\bfix (it|this|that)\b"),
    re.compile(r"\bmake it work\b"),
    re.compile(r"\bdo (it|the thing)\b"),
    re.compile(r"\bhandle it\b"),
)

#: Questions surfaced to the user when a goal is too vague to plan.
CLARIFYING_QUESTIONS = (
    "What specifically should change? (Which files, features, or behaviors?)",
    "What does 'done' look like — how will we verify the result?",
    "Are there constraints I should respect (scope, style, deadlines)?",
)


def _is_vague_goal(goal_text: str) -> bool:
    """Heuristic: the goal is too underspecified to plan safely.

    A goal is vague when it has fewer than three words (nothing to anchor a
    plan to) or matches a known content-free phrase such as "make it
    better". Runs *after* the software-goal check, so short but concrete
    goals like "implement quicksort" still get a real plan.
    """
    lowered = goal_text.strip().lower()
    if len(lowered.split()) < 3:
        return True
    return any(p.search(lowered) for p in _VAGUE_PATTERNS)


def _clarifying_plan(goal_id: str, goal_text: str) -> Plan:
    """A safe single-task plan that asks the user for specifics.

    The task's ``agent_capability`` is ``"supervise"`` so the supervisor
    agent picks it up; the supervisor short-circuits
    ``params["needs_clarification"]`` tasks (no routing, no side effects)
    and returns the clarifying questions. The scheduler treats the run as
    successful — the "work" was asking, not guessing.
    """
    task = TaskSpec(
        id="t1_clarify",
        name="Request clarification",
        description=(
            f"The goal {goal_text.strip()!r} is too vague to plan safely. "
            "Ask the user for the specifics below instead of guessing."
        ),
        agent_capability="supervise",
        params={
            "needs_clarification": True,
            "questions": list(CLARIFYING_QUESTIONS),
            "goal_hint": goal_text.strip(),
        },
    )
    return Plan(goal_id=goal_id, goal_text=goal_text, tasks=[task])


# ---------------------------------------------------------------------------
# Decomposition
# ---------------------------------------------------------------------------


def _software_plan(goal_id: str, goal_text: str) -> Plan:
    """The 7-task implement → test → repair → verify → approve DAG.

    Matches ARCHITECTURE.md §4. ``t4`` (diagnose/repair) only runs when ``t3``
    failed — expressed via ``params["conditional_on_failure_of"]`` which the
    scheduler honors.
    """
    target = _extract_target_name(goal_text)
    file_base = target or "solution"
    module_file = f"{file_base}.py"
    test_file = f"test_{file_base}.py"

    tasks = [
        TaskSpec(
            id="t1_write_impl",
            name="Write implementation",
            description=(
                f"Write the implementation of `{target or 'the requested function'}` "
                f"to {module_file} per the goal: {goal_text}"
            ),
            agent_capability="code",
            params={
                "kind": "implementation",
                "target_file": module_file,
                "function_name": target or "sort_list",
                "goal_hint": goal_text,
            },
        ),
        TaskSpec(
            id="t2_write_tests",
            name="Write unit tests",
            description=(
                f"Write unit tests for `{target or 'the implementation'}` to {test_file}, "
                f"covering the behavior described in the goal: {goal_text}"
            ),
            agent_capability="code",
            depends_on=["t1_write_impl"],
            params={
                "kind": "tests",
                "target_file": module_file,
                "test_file": test_file,
                "function_name": target or "sort_list",
                "goal_hint": goal_text,
            },
        ),
        TaskSpec(
            id="t3_run_tests",
            name="Run tests",
            description=f"Run the test suite in {test_file} in the sandbox and record a test report.",
            agent_capability="test",
            depends_on=["t2_write_tests"],
            params={"test_file": test_file},
        ),
        TaskSpec(
            id="t4_diagnose_repair",
            name="Diagnose and repair",
            description=(
                "Parse the failing test output, diagnose the bug, and apply a source "
                "patch via the workspace. Runs only if t3_run_tests failed."
            ),
            agent_capability="debug",
            depends_on=["t3_run_tests"],
            params={
                "conditional_on_failure_of": "t3_run_tests",
                "target_file": module_file,
                "test_file": test_file,
                "test_report": "TEST_REPORT.json",
            },
        ),
        TaskSpec(
            id="t5_rerun_tests",
            name="Re-run tests",
            description=f"Re-run the test suite in {test_file} in the sandbox after repair.",
            agent_capability="test",
            depends_on=["t4_diagnose_repair"],
            params={"test_file": test_file},
        ),
        TaskSpec(
            id="t6_review",
            name="Review artifacts",
            description=(
                f"Review {module_file} and {test_file} for correctness, style, and goal "
                "conformance; emit an approve/reject verdict."
            ),
            agent_capability="review",
            depends_on=["t5_rerun_tests"],
            params={"files": [module_file, test_file]},
        ),
        TaskSpec(
            id="t7_publish_request",
            name="Publish request",
            description=(
                "Request human approval to publish the reviewed artifacts. "
                "The kernel holds at the approval gate until resolved."
            ),
            agent_capability="review",
            depends_on=["t6_review"],
            approval_required=True,
            params={"action": "publish", "files": [module_file, test_file]},
        ),
    ]
    return Plan(goal_id=goal_id, goal_text=goal_text, tasks=tasks)


def _fallback_plan(goal_id: str, goal_text: str) -> Plan:
    """Generic 5-task pipeline for goals that do not match a known pattern."""
    tasks = [
        TaskSpec(
            id="t1_research",
            name="Research",
            description=f"Gather relevant context for the goal from memory and workspace: {goal_text}",
            agent_capability="research",
            params={"query": goal_text},
        ),
        TaskSpec(
            id="t2_plan_refine",
            name="Refine plan",
            description="Turn the research findings into a concrete execution outline.",
            agent_capability="plan",
            depends_on=["t1_research"],
        ),
        TaskSpec(
            id="t3_execute",
            name="Execute",
            description=f"Carry out the refined plan for the goal: {goal_text}",
            agent_capability="code",
            depends_on=["t2_plan_refine"],
            params={
                "kind": "implementation",
                "target_file": "solution.py",
                "function_name": "solution",
                "goal_hint": goal_text,
            },
        ),
        TaskSpec(
            id="t4_review",
            name="Review",
            description="Review the execution results for correctness and goal conformance.",
            agent_capability="review",
            depends_on=["t3_execute"],
        ),
        TaskSpec(
            id="t5_publish_request",
            name="Publish request",
            description=(
                "Request human approval to publish the results. "
                "The kernel holds at the approval gate until resolved."
            ),
            agent_capability="review",
            depends_on=["t4_review"],
            approval_required=True,
            params={"action": "publish"},
        ),
    ]
    return Plan(goal_id=goal_id, goal_text=goal_text, tasks=tasks)


def decompose_goal(goal_text: str, goal_id: str, model_router: Any = None) -> Plan:
    """Decompose a goal into a DAG of tasks.

    Dispatches on keyword/pattern rules:
      - empty/blank goals → ``ValueError`` (clear refusal; the kernel's
        ``create_goal`` already enforces this, this is defense in depth);
      - software goals (function/class mentions, "write"/"implement" verbs)
        → the 7-task implement→test→repair→verify→approve DAG;
      - vague goals (fewer than three words, or content-free phrases like
        "make it better") → a single-task clarifying plan; never a garbage
        DAG;
      - anything else → the generic 5-task research→plan→execute→review→approve pipeline.

    Args:
        goal_text: The natural-language goal.
        goal_id: Stable id carried onto the plan.
        model_router: Reserved hook for future LLM-assisted decomposition;
            accepted but currently unused.

    Returns:
        A ``Plan`` whose task ids are unique and descriptions non-empty.

    Raises:
        ValueError: If ``goal_text`` is empty or blank.
    """
    _ = model_router  # documented hook; rule-based for now
    if not goal_text or not goal_text.strip():
        raise ValueError(
            "goal text must be non-empty: refusing to build a plan from an empty goal"
        )
    if _is_software_goal(goal_text):
        return _software_plan(goal_id, goal_text)
    if _is_vague_goal(goal_text):
        return _clarifying_plan(goal_id, goal_text)
    return _fallback_plan(goal_id, goal_text)
