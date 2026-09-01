import json
from pathlib import Path
from threading import Lock
from typing import Any

import faiss
import numpy as np

from app.config import get_settings
from app.llm import LLMClient


def chunk_text(text: str, chunk_size: int = 900, overlap: int = 120) -> list[str]:
    cleaned = " ".join(text.split())
    if not cleaned:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(start + chunk_size, len(cleaned))
        chunks.append(cleaned[start:end])
        if end == len(cleaned):
            break
        start = max(end - overlap, start + 1)
    return chunks


class FaissStore:
    def __init__(
        self,
        index_path: str | Path | None = None,
        metadata_path: str | Path | None = None,
    ) -> None:
        settings = get_settings()

        self.index_path = Path(index_path or settings.index_path)
        self.metadata_path = Path(metadata_path or settings.metadata_path)

        self._lock = Lock()
        self.index: faiss.Index | None = None
        self.metadata: list[dict[str, Any]] = []

        self._load()

    def _load(self) -> None:
        if self.index_path.exists() and self.metadata_path.exists():
            self.index = faiss.read_index(str(self.index_path))
            self.metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        if self.index is not None:
            faiss.write_index(self.index, str(self.index_path))
        self.metadata_path.write_text(
            json.dumps(self.metadata, indent=2),
            encoding="utf-8",
        )

    def add_document(self, document_id: str, text: str, source: str) -> int:
        chunks = chunk_text(text)
        if not chunks:
            return 0

        vectors = np.asarray(LLMClient().embed(chunks), dtype="float32")
        faiss.normalize_L2(vectors)

        with self._lock:
            if self.index is None:
                self.index = faiss.IndexFlatIP(vectors.shape[1])
            elif self.index.d != vectors.shape[1]:
                raise ValueError("Embedding dimension does not match the existing index.")

            self.index.add(vectors)
            self.metadata.extend(
                {
                    "document_id": document_id,
                    "source": source,
                    "chunk_id": chunk_id,
                    "text": chunk,
                }
                for chunk_id, chunk in enumerate(chunks)
            )
            self._save()
        return len(chunks)

    def search(self, query: str, top_k: int = 4) -> list[dict[str, Any]]:
        if self.index is None or self.index.ntotal == 0:
            return []

        vector = np.asarray(LLMClient().embed([query]), dtype="float32")
        faiss.normalize_L2(vector)
        scores, indexes = self.index.search(vector, min(top_k, self.index.ntotal))

        results: list[dict[str, Any]] = []
        for score, index in zip(scores[0], indexes[0]):
            if index < 0:
                continue
            item = dict(self.metadata[index])
            item["score"] = round(float(score), 4)
            results.append(item)
        return results
