from typing import Literal

from app.llm import LLMClient


Route = Literal["retrieval", "direct"]


RETRIEVAL_KEYWORDS = {
    "policy",
    "policies",
    "internal",
    "company",
    "enterprise",
    "document",
    "documents",
    "deployment",
    "procedure",
    "procedures",
    "requirement",
    "requirements",
    "compliance",
    "knowledge base",
    "knowledge",
    "guideline",
    "guidelines",
    "standard",
    "standards",
}


def _requires_retrieval(query: str) -> bool:
    """
    Apply deterministic routing guardrails for clearly enterprise-specific
    or knowledge-base-dependent requests.
    """

    normalized = query.lower()

    return any(
        keyword in normalized
        for keyword in RETRIEVAL_KEYWORDS
    )


def supervisor_agent(query: str) -> Route:
    """
    Decide which execution path should handle a user request.

    Clear enterprise/document requests are deterministically routed
    to retrieval. Ambiguous requests are classified by the LLM.
    """

    if _requires_retrieval(query):
        return "retrieval"

    prompt = f"""
Classify the following user request into exactly one execution route.

User request:
{query}

Available routes:

retrieval
Use when the request requires enterprise documents, internal knowledge,
policies, indexed data, company-specific facts, procedures, standards,
or information that must be grounded in the knowledge base.

direct
Use only when the request can be answered without enterprise-specific
or indexed knowledge.

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

    return "retrieval"
