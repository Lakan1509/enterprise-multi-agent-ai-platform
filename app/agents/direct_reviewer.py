from app.llm import LLMClient


def direct_reviewer_agent(
    query: str,
    draft: str,
) -> str:
    """
    Review direct-response answers without enterprise grounding requirements.
    """

    prompt = f"""
User request:
{query}

Draft answer:
{draft}

Evaluate ONLY whether the draft satisfies the explicit user request.

Rules:
- Do not invent additional requirements.
- Do not require personalization unless the user explicitly asks for it.
- Do not require elaboration unless the user explicitly asks for it.
- Do not require a follow-up question unless the user explicitly asks for one.
- A short answer is acceptable for a short request.
- For a request such as "Say hello.", responses such as "Hello!",
  "Hi!", or "Hi, hello!" MUST be considered valid.
- The answer must not claim access to enterprise/internal documents.
- The answer must not fabricate citations.

Return exactly one of these formats:

PASS
The answer satisfies the user request.

OR

REVISE
Reason: <specific violation of the explicit user request>
Corrected answer:
<corrected response>
"""

    return LLMClient().complete(
        (
            "You are a precise direct-response quality reviewer. "
            "Judge only against explicit user requirements. "
            "Never invent additional preferences or quality criteria."
        ),
        prompt,
    )
