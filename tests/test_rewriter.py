from unittest.mock import patch

from app.agents.rewriter import rewriter_agent


@patch("app.agents.rewriter.LLMClient.complete")
def test_rewriter_uses_review_feedback(mock_complete):
    mock_complete.return_value = (
        "Production deployments require approval [policy:chunk-1]."
    )

    result = rewriter_agent(
        query="What is required for deployment?",
        draft="Deployment requires nothing.",
        review="REVISE\nReason: Unsupported claim.",
        retrieved_context=[
            {
                "document_id": "policy",
                "chunk_id": "chunk-1",
                "text": "Production deployments require approval.",
            }
        ],
    )

    assert result == (
        "Production deployments require approval [policy:chunk-1]."
    )

    system_prompt, user_prompt = mock_complete.call_args.args

    assert "correction agent" in system_prompt
    assert "Unsupported claim" in user_prompt
    assert "[policy:chunk-1]" in user_prompt
