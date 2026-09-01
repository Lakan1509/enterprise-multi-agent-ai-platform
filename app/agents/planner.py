from app.llm import LLMClient


def planner_agent(query: str) -> list[str]:
    """
    Create a concise execution plan for the incoming user task.

    The planner does not answer the question directly.
    It decomposes the task into 3-5 actionable steps that downstream
    agents can execute.
    """

    prompt = f"""
Create a concise 3-5 step execution plan for the following task.

Task:
{query}

Rules:
- Return one step per line.
- Do not number the steps.
- Keep each step short and actionable.
- Do not answer the task.
- Focus only on decomposition and execution strategy.
"""

    raw_plan = LLMClient().complete(
        "You are the planning agent in a production-grade multi-agent AI system.",
        prompt,
    )

    steps = [
        line.strip(" -•\t")
        for line in raw_plan.splitlines()
        if line.strip()
    ]

    return steps[:5]
