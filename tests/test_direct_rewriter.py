from unittest.mock import patch

from app.agents.direct_rewriter import direct_rewriter_agent


@patch("app.agents.direct_rewriter.LLMClient.complete")
def test_direct_rewriter_corrects_direct_answer(mock_complete):
    mock_complete.return_value = "Hello!"

    result = direct_rewriter_agent(
        query="Say hello.",
        draft="Something unrelated.",
        review="REVISE\nReason: Does not answer the request.",
    )

    assert result == "Hello!"

    system_prompt, user_prompt = mock_complete.call_args.args

    assert "direct-response correction agent" in system_prompt
    assert "Do not invent requirements" in user_prompt
