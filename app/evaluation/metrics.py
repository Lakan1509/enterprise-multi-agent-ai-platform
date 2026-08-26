from app.evaluation.golden import GoldenTaskResult
from app.evaluation.report import BatchEvaluationReport


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0

    ordered = sorted(values)

    index = max(
        0,
        int(0.95 * len(ordered) + 0.999999) - 1,
    )

    return ordered[index]


def build_batch_report(
    results: list[GoldenTaskResult],
) -> BatchEvaluationReport:
    total = len(results)

    if total == 0:
        return BatchEvaluationReport(
            total_tasks=0,
            passed_tasks=0,
            pass_rate=0.0,
            task_success_rate=0.0,
            groundedness_rate=0.0,
            hallucination_rate=0.0,
            average_latency_ms=0.0,
            p95_latency_ms=0.0,
            average_retries=0.0,
            average_tool_calls=0.0,
            results=[],
        )

    passed = sum(result.passed for result in results)

    return BatchEvaluationReport(
        total_tasks=total,
        passed_tasks=passed,
        pass_rate=passed / total,
        task_success_rate=(
            sum(result.task_success for result in results) / total
        ),
        groundedness_rate=(
            sum(result.grounded for result in results) / total
        ),
        hallucination_rate=(
            sum(
                result.hallucination_detected
                for result in results
            )
            / total
        ),
        average_latency_ms=(
            sum(result.latency_ms for result in results) / total
        ),
        p95_latency_ms=_p95(
            [result.latency_ms for result in results]
        ),
        average_retries=(
            sum(result.retry_count for result in results) / total
        ),
        average_tool_calls=(
            sum(result.tool_calls for result in results) / total
        ),
        results=results,
    )
