import json

import pytest

from app.evaluation.retrieval_runner import (
    load_golden_dataset,
    run_retrieval_evaluation,
)


class FakeStore:
    def search(self, query: str, top_k: int = 4):
        if "deployment" in query.lower():
            return [
                {
                    "document_id": "deployment-policy-001",
                    "chunk_id": 0,
                    "score": 0.95,
                },
                {
                    "document_id": "other-document",
                    "chunk_id": 0,
                    "score": 0.50,
                },
            ][:top_k]

        return [
            {
                "document_id": "wrong-document",
                "chunk_id": 0,
                "score": 0.40,
            }
        ][:top_k]


def test_load_golden_dataset(tmp_path):
    path = tmp_path / "golden.json"

    path.write_text(
        json.dumps(
            [
                {
                    "id": "task-1",
                    "query": "deployment requirements",
                    "expected_document_id": "deployment-policy-001",
                }
            ]
        ),
        encoding="utf-8",
    )

    dataset = load_golden_dataset(path)

    assert len(dataset) == 1
    assert dataset[0]["id"] == "task-1"


def test_real_retrieval_metrics_are_aggregated():
    dataset = [
        {
            "id": "task-1",
            "query": "deployment requirements",
            "expected_document_id": "deployment-policy-001",
        },
        {
            "id": "task-2",
            "query": "unrelated question",
            "expected_document_id": "missing-document",
        },
    ]

    report = run_retrieval_evaluation(
        FakeStore(),
        dataset,
        k=2,
    )

    assert report.total_tasks == 2
    assert report.hits == 1
    assert report.hit_rate_at_k == pytest.approx(0.5)
    assert report.recall_at_k == pytest.approx(0.5)
    assert report.mean_reciprocal_rank == pytest.approx(0.5)
    assert len(report.results) == 2


def test_empty_dataset():
    report = run_retrieval_evaluation(
        FakeStore(),
        [],
        k=4,
    )

    assert report.total_tasks == 0
    assert report.hit_rate_at_k == 0.0


def test_invalid_k():
    with pytest.raises(ValueError):
        run_retrieval_evaluation(
            FakeStore(),
            [],
            k=0,
        )
