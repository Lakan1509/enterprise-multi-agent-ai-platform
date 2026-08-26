from unittest.mock import patch

from app.agents.supervisor import supervisor_agent


@patch("app.agents.supervisor.LLMClient.complete")
def test_supervisor_routes_enterprise_question_to_retrieval(mock_complete):
    mock_complete.return_value = "retrieval"

    result = supervisor_agent(
        "What does our production deployment policy require?"
    )

    assert result == "retrieval"


@patch("app.agents.supervisor.LLMClient.complete")
def test_supervisor_routes_general_request_to_direct(mock_complete):
    mock_complete.return_value = "direct"

    result = supervisor_agent("Say hello.")

    assert result == "direct"


@patch("app.agents.supervisor.LLMClient.complete")
def test_supervisor_fails_safely_to_retrieval(mock_complete):
    mock_complete.return_value = "something-invalid"

    result = supervisor_agent("Unknown request")

    assert result == "retrieval"
