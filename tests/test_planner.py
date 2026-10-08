"""Tests for casi.planner.decompose_goal."""

from casi.planner import Plan, TaskSpec, decompose_goal

SORT_GOAL = "Write a Python function `sort_list` that sorts a list of numbers ascending, and include unit tests."


def _by_id(plan: Plan) -> dict[str, TaskSpec]:
    return {t.id: t for t in plan.tasks}


def test_sort_goal_produces_seven_task_dag():
    plan = decompose_goal(SORT_GOAL, "goal123")
    assert plan.goal_id == "goal123"
    assert plan.goal_text == SORT_GOAL
    assert len(plan.tasks) == 7
    ids = [t.id for t in plan.tasks]
    assert ids == [
        "t1_write_impl",
        "t2_write_tests",
        "t3_run_tests",
        "t4_diagnose_repair",
        "t5_rerun_tests",
        "t6_review",
        "t7_publish_request",
    ]
    # ids unique, descriptions non-empty
    assert len(set(ids)) == 7
    assert all(t.description.strip() for t in plan.tasks)


def test_sort_goal_dependencies_and_capabilities():
    tasks = _by_id(decompose_goal(SORT_GOAL, "g"))
    assert tasks["t1_write_impl"].depends_on == []
    assert tasks["t1_write_impl"].agent_capability == "code"
    assert tasks["t2_write_tests"].depends_on == ["t1_write_impl"]
    assert tasks["t3_run_tests"].depends_on == ["t2_write_tests"]
    assert tasks["t3_run_tests"].agent_capability == "test"
    t4 = tasks["t4_diagnose_repair"]
    assert t4.depends_on == ["t3_run_tests"]
    assert t4.agent_capability == "debug"
    assert t4.params["conditional_on_failure_of"] == "t3_run_tests"
    assert tasks["t5_rerun_tests"].depends_on == ["t4_diagnose_repair"]
    assert tasks["t6_review"].depends_on == ["t5_rerun_tests"]
    assert tasks["t6_review"].agent_capability == "review"
    t7 = tasks["t7_publish_request"]
    assert t7.depends_on == ["t6_review"]
    assert t7.approval_required is True
    assert t7.params["action"] == "publish"


def test_sort_goal_dag_is_acyclic():
    plan = decompose_goal(SORT_GOAL, "g")
    # Kahn's check inline: every dep must appear earlier in plan order
    seen: set[str] = set()
    for t in plan.tasks:
        assert all(d in seen for d in t.depends_on), f"{t.id} has forward dep"
        seen.add(t.id)


def test_software_goal_without_test_mention_still_gets_full_dag():
    plan = decompose_goal("Write a Python function `fib` computing fibonacci numbers.", "g")
    assert [t.id for t in plan.tasks][0] == "t1_write_impl"
    assert len(plan.tasks) == 7


def test_fallback_plan_for_generic_goal():
    plan = decompose_goal("Plan a team offsite in the mountains", "g9")
    ids = [t.id for t in plan.tasks]
    assert ids == ["t1_research", "t2_plan_refine", "t3_execute", "t4_review", "t5_publish_request"]
    tasks = _by_id(plan)
    assert tasks["t1_research"].agent_capability == "research"
    assert tasks["t2_plan_refine"].depends_on == ["t1_research"]
    assert tasks["t3_execute"].depends_on == ["t2_plan_refine"]
    assert tasks["t4_review"].depends_on == ["t3_execute"]
    t5 = tasks["t5_publish_request"]
    assert t5.depends_on == ["t4_review"]
    assert t5.approval_required is True
    assert all(t.description.strip() for t in plan.tasks)


def test_deterministic_and_model_router_hook_accepted():
    p1 = decompose_goal(SORT_GOAL, "g")
    p2 = decompose_goal(SORT_GOAL, "g", model_router=object())
    assert [t.id for t in p1.tasks] == [t.id for t in p2.tasks]
    assert [t.description for t in p1.tasks] == [t.description for t in p2.tasks]
