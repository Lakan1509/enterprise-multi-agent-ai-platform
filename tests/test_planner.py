from unittest.mock import patch

from app.agents.planner import planner_agent


@patch("app.agents.planner.LLMClient.complete")
def test_planner_agent_returns_max_five_steps(mock_complete):
    mock_complete.return_value = """
Understand the user request
Retrieve relevant context
Analyze the retrieved information
Validate factual accuracy
Generate the final response
Extra step that should be removed
"""

    result = planner_agent("Explain the deployment architecture.")

    assert len(result) == 5
    assert result[0] == "Understand the user request"
    assert result[-1] == "Generate the final response"


@patch("app.agents.planner.LLMClient.complete")
def test_planner_agent_removes_blank_lines_and_bullets(mock_complete):
    mock_complete.return_value = """
- Analyze the task

- Retrieve relevant information
- Produce the answer
"""

    result = planner_agent("Test query")

    assert result == [
        "Analyze the task",
        "Retrieve relevant information",
        "Produce the answer",
    ]
