from unittest.mock import patch

from app.agents.supervisor import (
    _requires_retrieval,
    supervisor_agent,
)


def test_policy_query_forces_retrieval_without_llm():
    assert _requires_retrieval(
        "What does the production deployment policy require?"
    )


def test_internal_document_query_forces_retrieval():
    assert _requires_retrieval(
        "Search our internal documents for the security standard."
    )


def test_simple_greeting_does_not_force_retrieval():
    assert not _requires_retrieval("Say hello.")


@patch("app.agents.supervisor.LLMClient.complete")
def test_supervisor_routes_enterprise_question_to_retrieval(
    mock_complete,
):
    result = supervisor_agent(
        "What does our production deployment policy require?"
    )

    assert result == "retrieval"
    mock_complete.assert_not_called()


@patch("app.agents.supervisor.LLMClient.complete")
def test_supervisor_routes_general_request_to_direct(mock_complete):
    mock_complete.return_value = "direct"

    result = supervisor_agent("Say hello.")

    assert result == "direct"


@patch("app.agents.supervisor.LLMClient.complete")
def test_supervisor_fails_safely_to_retrieval(mock_complete):
    mock_complete.return_value = "something-invalid"

    result = supervisor_agent("Tell me something interesting.")

    assert result == "retrieval"
