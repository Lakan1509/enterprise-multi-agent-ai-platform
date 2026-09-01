from app.llm import LLMClient


def direct_agent(query: str) -> str:
    """
    Handle requests that do not require enterprise knowledge retrieval.
    """

    prompt = f"""
User request:
{query}

Respond directly and concisely.

Rules:
- Do not claim access to internal enterprise documents.
- Do not fabricate citations.
- Do not invent facts.
- If the request requires enterprise-specific information, say that
  document retrieval is required.
"""

    return LLMClient().complete(
        "You are the direct-response agent in a production AI platform.",
        prompt,
    )
