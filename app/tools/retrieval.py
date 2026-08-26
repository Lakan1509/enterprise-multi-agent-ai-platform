from app.services.retrieval_service import RetrievalService
from app.tools.base import Tool


def search_knowledge_base(query: str) -> list[dict]:
    """
    Search the enterprise knowledge base.
    """
    return RetrievalService().search(query)


knowledge_search_tool = Tool(
    name="search_knowledge_base",
    description=(
        "Search indexed enterprise documents and return "
        "relevant grounded context."
    ),
    handler=search_knowledge_base,
)
