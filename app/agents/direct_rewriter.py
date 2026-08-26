from app.llm import LLMClient


def direct_rewriter_agent(
    query: str,
    draft: str,
    review: str,
) -> str:
    """
    Correct a direct-response answer using direct-response review feedback.
    """

    prompt = f"""
User request:
{query}

Previous draft:
{draft}

Reviewer feedback:
{review}

Correct the response using ONLY requirements explicitly present
in the user's request and legitimate issues identified by the reviewer.

Rules:
- Do not invent requirements.
- Do not add personalization unless requested.
- Do not add enterprise grounding requirements.
- Do not fabricate citations.
- Prefer the shortest answer that fully satisfies the request.
"""

    return LLMClient().complete(
        "You are a direct-response correction agent.",
        prompt,
    )
