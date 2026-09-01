from app.agents.grounded_answer import (
    grounded_answer_agent,
    normalize_citations,
)


def sample_context():
    return [
        {
            "document_id": "sample-company-policy",
            "chunk_id": 0,
            "source": "data/sample_company_policy.txt",
            "score": 0.9,
            "text": (
                "Acme AI Platform Policy. "
                "Production deployments require peer review, "
                "automated tests, and rollback plans. "
                "Customer documents must be encrypted in transit "
                "and at rest. "
                "Access follows the principle of least privilege. "
                "Critical services target 99.9 percent availability."
            ),
        }
    ]


def test_grounded_answer_handles_empty_context():
    result = grounded_answer_agent(
        query="What is the deployment policy?",
        retrieved_context=[],
    )

    assert "could not find enough information" in result.lower()


def test_grounded_answer_extracts_relevant_sentence():
    result = grounded_answer_agent(
        query=(
            "What does the production deployment "
            "policy require?"
        ),
        retrieved_context=sample_context(),
    )

    assert "peer review" in result
    assert "automated tests" in result
    assert "rollback plans" in result

    assert "encrypted" not in result
    assert "99.9" not in result

    assert "[sample-company-policy:0]" in result


def test_grounded_answer_ignores_unrelated_policy_facts():
    result = grounded_answer_agent(
        query="What do production deployments require?",
        retrieved_context=sample_context(),
    )

    assert "Production deployments require" in result
    assert "least privilege" not in result
    assert "availability" not in result


def test_normalize_citations_replaces_invalid_single_source():
    result = normalize_citations(
        "Deployment requires review [policy:0].",
        sample_context(),
    )

    assert "[sample-company-policy:0]" in result
    assert "[policy:0]" not in result


def test_normalize_citations_does_not_guess_multiple_sources():
    context = [
        {
            "document_id": "a",
            "chunk_id": 0,
            "text": "A",
        },
        {
            "document_id": "b",
            "chunk_id": 1,
            "text": "B",
        },
    ]

    answer = "Answer [unknown:0]."

    assert normalize_citations(answer, context) == answer
