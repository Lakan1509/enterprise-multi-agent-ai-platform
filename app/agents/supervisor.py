from typing import Literal

from app.llm import LLMClient


Route = Literal["retrieval", "direct"]


def supervisor_agent(query: str) -> Route:
    """
    Decide which execution path should handle the user request.

    retrieval:
        The request requires enterprise knowledge retrieval.

    direct:
        The request can be handled without document retrieval.
    """

    prompt = f"""
Classify the following user request into exactly one execution route.

User request:
{query}

Available routes:

retrieval
Use when the request asks about enterprise documents, policies,
internal knowledge, indexed data, or information that should be
grounded in the knowledge base.

direct
Use when the request does not require enterprise document retrieval.

Return ONLY one word:

retrieval

OR

direct
"""

    result = LLMClient().complete(
        "You are the routing supervisor for a production AI agent platform.",
        prompt,
    )

    route = result.strip().lower()

    if route == "direct":
        return "direct"

    # Fail safely toward grounded retrieval.
    return "retrieval"
