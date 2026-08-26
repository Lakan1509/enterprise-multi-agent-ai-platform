from app.evaluation.evaluator import evaluate_run


def test_evaluator_marks_grounded_success():
    result = evaluate_run(
        answer="Production deployments require approval.",
        review="PASS\nThe answer is fully grounded.",
        retry_count=0,
        latency_ms=125.5,
        tool_calls=1,
    )

    assert result.task_success is True
    assert result.grounded is True
    assert result.hallucination_detected is False
    assert result.retry_count == 0
    assert result.tool_calls == 1


def test_evaluator_marks_failed_grounding():
    result = evaluate_run(
        answer="Unsupported answer.",
        review="REVISE\nReason: Unsupported claim.",
        retry_count=2,
        latency_ms=200.0,
        tool_calls=1,
    )

    assert result.task_success is False
    assert result.grounded is False
    assert result.hallucination_detected is True
    assert result.retry_count == 2


def test_evaluator_requires_non_empty_answer():
    result = evaluate_run(
        answer="",
        review="PASS\nThe answer is fully grounded.",
        retry_count=0,
        latency_ms=50.0,
        tool_calls=0,
    )

    assert result.task_success is False


def test_direct_route_does_not_require_grounding_review():
    result = evaluate_run(
        answer="Hello!",
        review="",
        retry_count=0,
        latency_ms=10.0,
        tool_calls=0,
        route="direct",
    )

    assert result.task_success is True
    assert result.grounded is True
    assert result.hallucination_detected is False
    assert result.retry_count == 0
