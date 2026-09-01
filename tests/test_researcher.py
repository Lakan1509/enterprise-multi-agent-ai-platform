from unittest.mock import patch

from app.agents.researcher import researcher_agent


def test_researcher_returns_fallback_when_context_is_empty():
    result = researcher_agent(
        query="What is the deployment policy?",
        retrieved_context=[],
    )

    assert "No relevant internal document context was found" in result


@patch("app.agents.researcher.LLMClient.complete")
def test_researcher_builds_grounded_prompt(mock_complete):
    mock_complete.return_value = (
        "Production deployments require approval [policy:chunk-1]."
    )

    context = [
        {
            "document_id": "policy",
            "chunk_id": "chunk-1",
            "text": "Production deployments require approval.",
        }
    ]

    result = researcher_agent(
        query="What is required for production deployment?",
        retrieved_context=context,
    )

    assert result == (
        "Production deployments require approval [policy:chunk-1]."
    )

    mock_complete.assert_called_once()

    system_prompt, user_prompt = mock_complete.call_args.args

    assert "strict enterprise document research agent" in system_prompt
    assert "[policy:chunk-1]" in user_prompt
    assert "Production deployments require approval." in user_prompt
    assert "Do not use outside knowledge" in user_prompt
