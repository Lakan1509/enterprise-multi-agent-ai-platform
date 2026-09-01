from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class RetrievalMetricResult:
    recall_at_k: float
    hit_at_k: bool
    reciprocal_rank: float


def _extract_document_id(item: object) -> str | None:
    if isinstance(item, dict):
        return (
            item.get("document_id")
            or item.get("doc_id")
            or item.get("source_id")
        )

    for attr in ("document_id", "doc_id", "source_id"):
        value = getattr(item, attr, None)
        if value is not None:
            return str(value)

    metadata = getattr(item, "metadata", None)
    if isinstance(metadata, dict):
        return (
            metadata.get("document_id")
            or metadata.get("doc_id")
            or metadata.get("source_id")
        )

    return None


def evaluate_retrieval(
    retrieved_items: Sequence[object] | Iterable[object],
    expected_document_id: str,
    k: int = 5,
) -> RetrievalMetricResult:
    if k <= 0:
        raise ValueError("k must be greater than 0")

    items = list(retrieved_items)[:k]
    retrieved_ids = [_extract_document_id(item) for item in items]

    try:
        rank = retrieved_ids.index(expected_document_id) + 1
    except ValueError:
        rank = None

    hit = rank is not None

    return RetrievalMetricResult(
        recall_at_k=1.0 if hit else 0.0,
        hit_at_k=hit,
        reciprocal_rank=(1.0 / rank) if rank else 0.0,
    )
