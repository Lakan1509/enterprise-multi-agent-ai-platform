"""Run the isolated FAISS retrieval benchmark."""

import json
from datetime import datetime, timezone
from pathlib import Path

from app.evaluation.retrieval_runner import (
    report_to_dict,
    run_retrieval_evaluation,
)
from app.vector_store import FaissStore


DOCS = [
    ("deployment-policy-001", "data/eval_corpus/deployment_policy.txt"),
    ("api-security-policy-001", "data/eval_corpus/api_security_policy.txt"),
    ("observability-policy-001", "data/eval_corpus/observability_policy.txt"),
    ("model-governance-policy-001", "data/eval_corpus/model_governance_policy.txt"),
    ("data-governance-policy-001", "data/eval_corpus/data_governance_policy.txt"),
]

DATASET = [
    {
        "id": "deployment-1",
        "query": "What controls are required before deploying an AI system to production?",
        "expected_document_id": "deployment-policy-001",
    },
    {
        "id": "security-1",
        "query": "How should production AI APIs be protected?",
        "expected_document_id": "api-security-policy-001",
    },
    {
        "id": "observability-1",
        "query": "What observability capabilities should production AI services have?",
        "expected_document_id": "observability-policy-001",
    },
    {
        "id": "governance-1",
        "query": "What governance is required for production machine-learning models?",
        "expected_document_id": "model-governance-policy-001",
    },
    {
        "id": "data-1",
        "query": "What controls should be applied to production datasets?",
        "expected_document_id": "data-governance-policy-001",
    },
]


def main() -> None:
    runtime_dir = Path("data/evaluation/runtime")
    results_dir = Path("data/evaluation/results")

    runtime_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    store = FaissStore(
        index_path=runtime_dir / "faiss.index",
        metadata_path=runtime_dir / "metadata.json",
    )

    # Rebuild the isolated benchmark index from scratch.
    store.index = None
    store.metadata = []

    print("=== BUILDING EVALUATION INDEX ===")

    for document_id, file_path in DOCS:
        text = Path(file_path).read_text(encoding="utf-8")

        chunks = store.add_document(
            document_id=document_id,
            text=text,
            source=file_path,
        )

        print(f"{document_id}: {chunks} chunk(s)")

    print("\n=== RUNNING RETRIEVAL BENCHMARK ===")

    report = run_retrieval_evaluation(
        store=store,
        dataset=DATASET,
        k=4,
    )

    result = report_to_dict(report)

    result["benchmark"] = {
        "name": "Synthetic Enterprise Policy Retrieval Benchmark",
        "corpus_documents": len(DOCS),
        "queries": len(DATASET),
        "top_k": 4,
        "embedding_model": "embeddinggemma",
        "vector_store": "FAISS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    output_path = results_dir / "retrieval_benchmark.json"

    output_path.write_text(
        json.dumps(result, indent=2),
        encoding="utf-8",
    )

    print()
    print("=== RESULTS ===")
    print(f"Tasks: {report.total_tasks}")
    print(f"Hits: {report.hits}")
    print(f"Hit@4: {report.hit_rate_at_k:.3f}")
    print(f"Recall@4: {report.recall_at_k:.3f}")
    print(f"MRR: {report.mean_reciprocal_rank:.3f}")
    print(f"Average latency: {report.average_latency_ms:.3f} ms")

    print("\nPer-query results:")

    for item in report.results:
        print(
            f"  {item.task_id}: "
            f"hit={item.hit_at_k}, "
            f"rr={item.reciprocal_rank:.3f}, "
            f"latency={item.latency_ms:.3f} ms"
        )

    print(f"\nSaved: {output_path}")


if __name__ == "__main__":
    main()
