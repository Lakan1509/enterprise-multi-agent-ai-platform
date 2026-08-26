from typing import Literal


RetrievalMode = Literal["fast", "research"]


RESEARCH_KEYWORDS = {
    "compare",
    "analyze",
    "analysis",
    "summarize",
    "summary",
    "explain in detail",
    "multiple documents",
    "across documents",
    "tradeoff",
    "tradeoffs",
    "recommend",
    "recommendation",
    "investigate",
    "research",
}


def retrieval_router(query: str) -> RetrievalMode:
    """
    Decide whether a retrieval request needs the full research pipeline
    or can use the fast grounded-answer path.
    """

    normalized = query.lower()

    if any(keyword in normalized for keyword in RESEARCH_KEYWORDS):
        return "research"

    return "fast"
