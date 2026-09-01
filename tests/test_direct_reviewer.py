from unittest.mock import patch

from app.agents.direct_reviewer import direct_reviewer_agent


@patch("app.agents.direct_reviewer.LLMClient.complete")
def test_direct_reviewer_accepts_simple_response(mock_complete):
    mock_complete.return_value = (
        "PASS\nThe answer satisfies the user request."
    )

    result = direct_reviewer_agent(
        query="Say hello.",
        draft="Hi, hello!",
    )

    assert result.startswith("PASS")

    system_prompt, user_prompt = mock_complete.call_args.args

    assert "explicit user requirements" in system_prompt
    assert "Hi, hello!" in user_prompt
    assert "Do not require personalization" in user_prompt
    assert '"Say hello."' in user_prompt


@patch("app.agents.direct_reviewer.LLMClient.complete")
def test_direct_reviewer_does_not_require_personalization(mock_complete):
    mock_complete.return_value = (
        "PASS\nThe answer satisfies the user request."
    )

    result = direct_reviewer_agent(
        query="Say hello.",
        draft="Hello!",
    )

    assert result.startswith("PASS")
