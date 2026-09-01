from unittest.mock import patch

from app.evaluation.golden import GoldenTask
from app.evaluation.runner import BatchEvaluationRunner


@patch("app.evaluation.runner.graph.invoke")
def test_runner_passes_matching_task(mock_invoke):
    mock_invoke.return_value = {
        "answer": (
            "Production deployments require approval "
            "[policy:1]."
        ),
        "review": "PASS\nThe answer is fully grounded.",
        "route": "retrieval",
        "retry_count": 0,
        "retrieved_context": [
            {
                "document_id": "policy",
                "chunk_id": 1,
                "source": "manual",
                "score": 0.98,
                "text": (
                    "Production deployments require approval."
                ),
            }
        ],
    }

    task = GoldenTask(
        task_id="deployment-policy",
        query="What is required for production deployment?",
        expected_route="retrieval",
        expected_answer_contains=["require approval"],
        expected_citations=["policy:1"],
        max_retries=2,
    )

    result = BatchEvaluationRunner().run_task(task)

    assert result.passed is True
    assert result.answer_match is True
    assert result.route_match is True
    assert result.citation_match is True
    assert result.task_success is True


@patch("app.evaluation.runner.graph.invoke")
def test_runner_fails_wrong_route(mock_invoke):
    mock_invoke.return_value = {
        "answer": "Hello!",
        "review": "PASS\nThe answer is fully grounded.",
        "route": "direct",
        "retry_count": 0,
        "retrieved_context": [],
    }

    task = GoldenTask(
        task_id="wrong-route",
        query="What is our deployment policy?",
        expected_route="retrieval",
    )

    result = BatchEvaluationRunner().run_task(task)

    assert result.route_match is False
    assert result.passed is False


@patch("app.evaluation.runner.graph.invoke")
def test_batch_runner_builds_report(mock_invoke):
    mock_invoke.side_effect = [
        {
            "answer": "Hello!",
            "review": "PASS\nThe answer is fully grounded.",
            "route": "direct",
            "retry_count": 0,
            "retrieved_context": [],
        },
        {
            "answer": "Goodbye!",
            "review": "PASS\nThe answer is fully grounded.",
            "route": "direct",
            "retry_count": 0,
            "retrieved_context": [],
        },
    ]

    tasks = [
        GoldenTask(
            task_id="task-1",
            query="Say hello.",
            expected_route="direct",
            expected_answer_contains=["hello"],
        ),
        GoldenTask(
            task_id="task-2",
            query="Say goodbye.",
            expected_route="direct",
            expected_answer_contains=["goodbye"],
        ),
    ]

    report = BatchEvaluationRunner().run_batch(tasks)

    assert report.total_tasks == 2
    assert report.passed_tasks == 2
    assert report.pass_rate == 1.0
