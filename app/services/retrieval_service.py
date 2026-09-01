from app.config import get_settings
from app.vector_store import FaissStore


class RetrievalService:
    """
    Production-facing retrieval service.

    Encapsulates vector-store access so agents do not depend directly
    on the underlying FAISS implementation.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.store = FaissStore()

    def search(self, query: str) -> list[dict]:
        if not query or not query.strip():
            return []

        return self.store.search(
            query.strip(),
            top_k=self.settings.top_k,
        )
