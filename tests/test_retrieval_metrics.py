import pytest

from app.evaluation.retrieval_metrics import evaluate_retrieval


def test_retrieval_hit_at_k():
    retrieved = [
        {"document_id": "other-doc"},
        {"document_id": "deployment-policy-001"},
    ]

    result = evaluate_retrieval(
        retrieved,
        expected_document_id="deployment-policy-001",
        k=5,
    )

    assert result.hit_at_k is True
    assert result.recall_at_k == 1.0
    assert result.reciprocal_rank == 0.5


def test_retrieval_miss_at_k():
    retrieved = [
        {"document_id": "other-doc-1"},
        {"document_id": "other-doc-2"},
    ]

    result = evaluate_retrieval(
        retrieved,
        expected_document_id="deployment-policy-001",
        k=2,
    )

    assert result.hit_at_k is False
    assert result.recall_at_k == 0.0
    assert result.reciprocal_rank == 0.0


def test_retrieval_respects_k():
    retrieved = [
        {"document_id": "other-doc"},
        {"document_id": "deployment-policy-001"},
    ]

    result = evaluate_retrieval(
        retrieved,
        expected_document_id="deployment-policy-001",
        k=1,
    )

    assert result.hit_at_k is False


def test_invalid_k():
    with pytest.raises(ValueError):
        evaluate_retrieval([], "deployment-policy-001", k=0)
