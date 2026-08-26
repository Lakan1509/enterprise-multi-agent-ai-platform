from time import perf_counter
from uuid import uuid4

from app.evaluation.evaluator import evaluate_run
from app.evaluation.golden import GoldenTask, GoldenTaskResult
from app.evaluation.metrics import build_batch_report
from app.evaluation.report import BatchEvaluationReport
from app.graph import graph


class BatchEvaluationRunner:
    """
    Execute golden tasks against the agent graph and produce aggregate metrics.
    """

    def run_task(
        self,
        task: GoldenTask,
    ) -> GoldenTaskResult:
        thread_id = f"eval-{task.task_id}-{uuid4()}"

        start_time = perf_counter()

        result = graph.invoke(
            {
                "query": task.query,
                "retry_count": 0,
            },
            config={
                "configurable": {
                    "thread_id": thread_id,
                }
            },
        )

        latency_ms = (perf_counter() - start_time) * 1000

        answer = result.get("answer", "")
        review = result.get("review", "")
        route = result.get("route", "retrieval")
        retry_count = result.get("retry_count", 0)
        citations = result.get("retrieved_context", [])

        tool_calls = 1 if route == "retrieval" else 0

        evaluation = evaluate_run(
            answer=answer,
            review=review,
            retry_count=retry_count,
            latency_ms=latency_ms,
            tool_calls=tool_calls,
            route=route,
        )

        answer_lower = answer.lower()

        answer_match = all(
            expected.lower() in answer_lower
            for expected in task.expected_answer_contains
        )

        route_match = (
            task.expected_route is None
            or route == task.expected_route
        )

        actual_citations = {
            f"{item.get('document_id')}:{item.get('chunk_id')}"
            for item in citations
        }

        citation_match = all(
            expected in actual_citations
            for expected in task.expected_citations
        )

        retry_limit_passed = (
            retry_count <= task.max_retries
        )

        latency_limit_passed = (
            task.max_latency_ms is None
            or latency_ms <= task.max_latency_ms
        )

        passed = all(
            [
                evaluation.task_success,
                answer_match,
                route_match,
                citation_match,
                retry_limit_passed,
                latency_limit_passed,
            ]
        )

        return GoldenTaskResult(
            task_id=task.task_id,
            passed=passed,
            answer_match=answer_match,
            route_match=route_match,
            citation_match=citation_match,
            retry_limit_passed=retry_limit_passed,
            latency_limit_passed=latency_limit_passed,
            task_success=evaluation.task_success,
            grounded=evaluation.grounded,
            hallucination_detected=(
                evaluation.hallucination_detected
            ),
            retry_count=retry_count,
            latency_ms=latency_ms,
            tool_calls=tool_calls,
        )

    def run_batch(
        self,
        tasks: list[GoldenTask],
    ) -> BatchEvaluationReport:
        results = []
        total = len(tasks)

        for index, task in enumerate(tasks, start=1):
            print(
                f"[{index}/{total}] Running "
                f"{task.task_id}...",
                flush=True,
            )

            start_time = perf_counter()

            result = self.run_task(task)

            elapsed_seconds = perf_counter() - start_time

            results.append(result)

            status = "PASS" if result.passed else "FAIL"

            print(
                f"[{index}/{total}] {status} "
                f"{task.task_id} "
                f"({elapsed_seconds:.2f}s)",
                flush=True,
            )

        return build_batch_report(results)
