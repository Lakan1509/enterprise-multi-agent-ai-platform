from app.evaluation.golden import GoldenTaskResult
from app.evaluation.metrics import build_batch_report


def make_result(
    task_id: str,
    *,
    passed: bool,
    latency_ms: float,
    grounded: bool = True,
    hallucination_detected: bool = False,
    retry_count: int = 0,
    tool_calls: int = 1,
) -> GoldenTaskResult:
    return GoldenTaskResult(
        task_id=task_id,
        passed=passed,
        answer_match=passed,
        route_match=True,
        citation_match=True,
        retry_limit_passed=True,
        latency_limit_passed=True,
        task_success=passed,
        grounded=grounded,
        hallucination_detected=hallucination_detected,
        retry_count=retry_count,
        latency_ms=latency_ms,
        tool_calls=tool_calls,
    )


def test_batch_report_calculates_rates():
    results = [
        make_result(
            "task-1",
            passed=True,
            latency_ms=100,
        ),
        make_result(
            "task-2",
            passed=False,
            latency_ms=200,
            grounded=False,
            hallucination_detected=True,
            retry_count=2,
        ),
    ]

    report = build_batch_report(results)

    assert report.total_tasks == 2
    assert report.passed_tasks == 1

    assert report.pass_rate == 0.5
    assert report.task_success_rate == 0.5
    assert report.groundedness_rate == 0.5
    assert report.hallucination_rate == 0.5

    assert report.average_latency_ms == 150
    assert report.p95_latency_ms == 200
    assert report.average_retries == 1
    assert report.average_tool_calls == 1


def test_empty_batch_report():
    report = build_batch_report([])

    assert report.total_tasks == 0
    assert report.pass_rate == 0
    assert report.p95_latency_ms == 0
