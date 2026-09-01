from app.services.retrieval_service import RetrievalService


def retriever_agent(query: str) -> list[dict]:
    """
    Retrieve relevant enterprise knowledge for a user query.
    """

    service = RetrievalService()
    return service.search(query)
