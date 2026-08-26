from unittest.mock import patch

from app.agents.grounded_answer import grounded_answer_agent


def test_grounded_answer_handles_empty_context():
    result = grounded_answer_agent(
        query="What is the deployment policy?",
        retrieved_context=[],
    )

    assert "could not find enough information" in result.lower()


@patch("app.agents.grounded_answer.LLMClient.complete")
def test_grounded_answer_uses_context_and_citations(mock_complete):
    mock_complete.return_value = (
        "Production deployments require peer review, automated tests, "
        "and rollback plans [sample-company-policy:0]."
    )

    context = [
        {
            "document_id": "sample-company-policy",
            "chunk_id": 0,
            "text": (
                "Production deployments require peer review, "
                "automated tests, and rollback plans."
            ),
            "source": "test",
            "score": 0.9,
        }
    ]

    result = grounded_answer_agent(
        query="What does the production deployment policy require?",
        retrieved_context=context,
    )

    assert "peer review" in result
    assert "[sample-company-policy:0]" in result

    _, prompt = mock_complete.call_args.args

    assert "automated tests" in prompt
    assert "Do not include unrelated facts" in prompt
