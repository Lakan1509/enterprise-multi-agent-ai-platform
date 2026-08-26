from unittest.mock import patch

from app.agents.direct import direct_agent


@patch("app.agents.direct.LLMClient.complete")
def test_direct_agent_returns_response(mock_complete):
    mock_complete.return_value = "Hello! How can I help?"

    result = direct_agent("Say hello.")

    assert result == "Hello! How can I help?"

    system_prompt, user_prompt = mock_complete.call_args.args

    assert "direct-response agent" in system_prompt
    assert "Say hello." in user_prompt
    assert "Do not fabricate citations" in user_prompt
