from unittest.mock import patch

from app.agents.reviewer import finalize_review, reviewer_agent
from app.agents.writer import writer_agent


@patch("app.agents.writer.LLMClient.complete")
def test_writer_agent_uses_research_notes(mock_complete):
    mock_complete.return_value = (
        "Production deployments require approval [policy:chunk-1]."
    )

    result = writer_agent(
        query="What is required for production deployment?",
        research_notes=(
            "Production deployments require approval [policy:chunk-1]."
        ),
    )

    assert "Production deployments require approval" in result

    _, user_prompt = mock_complete.call_args.args

    assert "Do not add outside knowledge" in user_prompt
    assert "[policy:chunk-1]" in user_prompt


@patch("app.agents.reviewer.LLMClient.complete")
def test_reviewer_agent_receives_allowed_context(mock_complete):
    mock_complete.return_value = "PASS\nThe answer is fully grounded."

    context = [
        {
            "document_id": "policy",
            "chunk_id": "chunk-1",
            "text": "Production deployments require approval.",
        }
    ]

    result = reviewer_agent(
        query="What is required?",
        retrieved_context=context,
        draft=(
            "Production deployments require approval "
            "[policy:chunk-1]."
        ),
    )

    assert result.startswith("PASS")

    _, user_prompt = mock_complete.call_args.args

    assert "[policy:chunk-1]" in user_prompt
    assert "Production deployments require approval." in user_prompt


def test_finalize_review_returns_corrected_answer():
    review = """
REVISE
Reason: Unsupported claim.
Corrected answer:
Production deployments require approval [policy:chunk-1].
"""

    result = finalize_review(
        review=review,
        draft="Incorrect answer.",
    )

    assert result == (
        "Production deployments require approval [policy:chunk-1]."
    )


def test_finalize_review_returns_original_draft_on_pass():
    result = finalize_review(
        review="PASS\nThe answer is fully grounded.",
        draft="Grounded answer [policy:chunk-1].",
    )

    assert result == "Grounded answer [policy:chunk-1]."
