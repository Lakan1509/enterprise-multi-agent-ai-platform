import json
import sys
from pathlib import Path

from app.evaluation.golden import GoldenTask
from app.evaluation.runner import BatchEvaluationRunner


def load_tasks(path: Path) -> list[GoldenTask]:
    data = json.loads(path.read_text())

    if not isinstance(data, list):
        raise ValueError("Golden task file must contain a JSON list.")

    return [
        GoldenTask.model_validate(item)
        for item in data
    ]


def print_report(report) -> None:
    print()
    print("=== Agent Evaluation Report ===")
    print(f"Total tasks:            {report.total_tasks}")
    print(f"Passed tasks:           {report.passed_tasks}")
    print(f"Pass rate:              {report.pass_rate:.1%}")
    print(f"Task success rate:      {report.task_success_rate:.1%}")
    print(f"Groundedness rate:      {report.groundedness_rate:.1%}")
    print(f"Hallucination rate:     {report.hallucination_rate:.1%}")
    print(f"Average latency:        {report.average_latency_ms:.2f} ms")
    print(f"P95 latency:            {report.p95_latency_ms:.2f} ms")
    print(f"Average retries:        {report.average_retries:.2f}")
    print(f"Average tool calls:     {report.average_tool_calls:.2f}")
    print()

    for result in report.results:
        status = "PASS" if result.passed else "FAIL"

        print(
            f"{status:4} | "
            f"{result.task_id:24} | "
            f"latency={result.latency_ms:.2f}ms | "
            f"retries={result.retry_count} | "
            f"tools={result.tool_calls}"
        )


def main() -> None:
    if len(sys.argv) != 2:
        print(
            "Usage: python -m app.evaluation.cli "
            "data/golden_tasks.json"
        )
        raise SystemExit(1)

    path = Path(sys.argv[1])

    if not path.exists():
        print(f"Golden task file not found: {path}")
        raise SystemExit(1)

    tasks = load_tasks(path)

    report = BatchEvaluationRunner().run_batch(tasks)

    print_report(report)


if __name__ == "__main__":
    main()
