from unittest.mock import patch

from app.agents.retriever import retriever_agent
from app.services.retrieval_service import RetrievalService


@patch.object(RetrievalService, "search")
def test_retriever_agent_returns_search_results(mock_search):
    mock_search.return_value = [
        {
            "document_id": "doc-1",
            "chunk_id": "chunk-1",
            "text": "Example enterprise context",
        }
    ]

    result = retriever_agent("What is the deployment policy?")

    assert len(result) == 1
    assert result[0]["document_id"] == "doc-1"
    mock_search.assert_called_once_with(
        "What is the deployment policy?"
    )


@patch("app.services.retrieval_service.FaissStore")
def test_retrieval_service_does_not_search_empty_query(mock_store):
    service = RetrievalService()

    result = service.search("   ")

    assert result == []
    mock_store.return_value.search.assert_not_called()
