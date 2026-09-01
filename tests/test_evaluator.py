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


def test_reviewer_corrected_answer_counts_as_grounded():
    result = evaluate_run(
        answer="Deployments require approval [policy:0].",
        review=(
            "REVISE\n"
            "Reason: Missing citation.\n"
            "Corrected answer:\n"
            "Deployments require approval [policy:0]."
        ),
        retry_count=0,
        latency_ms=100.0,
        tool_calls=1,
        route="retrieval",
    )

    assert result.task_success is True
    assert result.grounded is True
    assert result.retry_count == 0


def test_corrected_unsupported_draft_is_not_final_hallucination():
    result = evaluate_run(
        answer=(
            "Production deployments require peer review "
            "[sample-company-policy:0]."
        ),
        review=(
            "REVISE\n"
            "Reason: Previous draft contained an unsupported claim.\n"
            "Corrected answer:\n"
            "Production deployments require peer review "
            "[sample-company-policy:0]."
        ),
        retry_count=0,
        latency_ms=100.0,
        tool_calls=1,
        route="retrieval",
    )

    assert result.task_success is True
    assert result.grounded is True
    assert result.hallucination_detected is False


def test_evaluator_records_tool_observability_metrics():
    result = evaluate_run(
        answer="Grounded answer [policy:0].",
        review="PASS\nThe answer is fully grounded.",
        retry_count=0,
        latency_ms=100.0,
        tool_calls=2,
        tool_success_count=1,
        tool_failure_count=1,
        tool_latency_ms=25.5,
        route="retrieval",
    )

    assert result.tool_calls == 2
    assert result.tool_success_count == 1
    assert result.tool_failure_count == 1
    assert result.tool_latency_ms == 25.5
