from unittest.mock import patch

from app.main import query
from app.models import QueryRequest


@patch("app.main.graph.invoke")
def test_query_returns_evaluation_metrics(mock_invoke):
    def fake_invoke(payload, config):
        trace = payload["trace"]

        from app.observability.tracing import ToolCallTrace

        trace.tool_calls.append(
            ToolCallTrace(
                tool_name="search_knowledge_base",
                success=True,
                latency_ms=5.0,
            )
        )

        return {
            "answer": (
                "Production deployments require approval "
                "[policy:1]."
            ),
            "plan": [
                "Retrieve policy",
                "Validate answer",
            ],
            "retrieved_context": [
                {
                    "document_id": "policy",
                    "source": "manual",
                    "chunk_id": 1,
                    "score": 0.95,
                    "text": (
                        "Production deployments require approval."
                    ),
                }
            ],
            "review": "PASS\nThe answer is fully grounded.",
            "retry_count": 1,
            "route": "retrieval",
        }

    mock_invoke.side_effect = fake_invoke

    response = query(
        QueryRequest(
            query="What is required for production deployment?",
            thread_id="test-thread",
        )
    )

    assert response.answer.startswith(
        "Production deployments require approval"
    )

    assert response.evaluation.task_success is True
    assert response.evaluation.grounded is True
    assert response.evaluation.retry_count == 1
    assert response.evaluation.tool_calls == 1
    assert response.evaluation.latency_ms >= 0

    assert response.metadata["route"] == "retrieval"
    assert response.metadata["thread_id"] == "test-thread"


@patch("app.main.graph.invoke")
def test_direct_route_reports_zero_tool_calls(mock_invoke):
    mock_invoke.return_value = {
        "answer": "Hello!",
        "plan": [
            "Respond directly",
        ],
        "retrieved_context": [],
        "review": "PASS\nThe answer is fully grounded.",
        "retry_count": 0,
        "route": "direct",
    }

    response = query(
        QueryRequest(
            query="Say hello.",
            thread_id="direct-test",
        )
    )

    assert response.evaluation.tool_calls == 0
    assert response.evaluation.task_success is True
    assert response.metadata["route"] == "direct"
