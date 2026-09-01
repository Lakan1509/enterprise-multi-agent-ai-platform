from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter

from app.evaluation.retrieval_metrics import evaluate_retrieval
from app.vector_store import FaissStore


@dataclass(frozen=True)
class RetrievalEvaluationResult:
    task_id: str
    query: str
    expected_document_id: str
    hit_at_k: bool
    recall_at_k: float
    reciprocal_rank: float
    latency_ms: float
    retrieved_document_ids: list[str]


@dataclass(frozen=True)
class RetrievalEvaluationReport:
    total_tasks: int
    hits: int
    hit_rate_at_k: float
    recall_at_k: float
    mean_reciprocal_rank: float
    average_latency_ms: float
    results: list[RetrievalEvaluationResult]


def load_golden_dataset(path: str | Path) -> list[dict]:
    dataset_path = Path(path)

    with dataset_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError("Golden dataset must contain a JSON list.")

    return data


def run_retrieval_evaluation(
    store: FaissStore,
    dataset: list[dict],
    k: int = 4,
) -> RetrievalEvaluationReport:
    if k <= 0:
        raise ValueError("k must be greater than 0")

    results: list[RetrievalEvaluationResult] = []

    for task in dataset:
        task_id = str(task["id"])
        query = str(task["query"])
        expected_document_id = str(task["expected_document_id"])

        start = perf_counter()
        retrieved = store.search(query, top_k=k)
        latency_ms = (perf_counter() - start) * 1000

        metrics = evaluate_retrieval(
            retrieved,
            expected_document_id=expected_document_id,
            k=k,
        )

        retrieved_document_ids = [
            str(item.get("document_id"))
            for item in retrieved
            if item.get("document_id") is not None
        ]

        results.append(
            RetrievalEvaluationResult(
                task_id=task_id,
                query=query,
                expected_document_id=expected_document_id,
                hit_at_k=metrics.hit_at_k,
                recall_at_k=metrics.recall_at_k,
                reciprocal_rank=metrics.reciprocal_rank,
                latency_ms=round(latency_ms, 3),
                retrieved_document_ids=retrieved_document_ids,
            )
        )

    total = len(results)

    if total == 0:
        return RetrievalEvaluationReport(
            total_tasks=0,
            hits=0,
            hit_rate_at_k=0.0,
            recall_at_k=0.0,
            mean_reciprocal_rank=0.0,
            average_latency_ms=0.0,
            results=[],
        )

    hits = sum(result.hit_at_k for result in results)

    return RetrievalEvaluationReport(
        total_tasks=total,
        hits=hits,
        hit_rate_at_k=hits / total,
        recall_at_k=sum(r.recall_at_k for r in results) / total,
        mean_reciprocal_rank=sum(r.reciprocal_rank for r in results) / total,
        average_latency_ms=(
            sum(r.latency_ms for r in results) / total
        ),
        results=results,
    )


def report_to_dict(report: RetrievalEvaluationReport) -> dict:
    return asdict(report)
