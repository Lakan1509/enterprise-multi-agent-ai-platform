from app.agents.retrieval_router import retrieval_router


def test_simple_policy_lookup_uses_fast_path():
    assert (
        retrieval_router(
            "What does the production deployment policy require?"
        )
        == "fast"
    )


def test_comparison_uses_research_path():
    assert (
        retrieval_router(
            "Compare the deployment and security policies."
        )
        == "research"
    )


def test_detailed_analysis_uses_research_path():
    assert (
        retrieval_router(
            "Analyze the internal policies and explain the tradeoffs."
        )
        == "research"
    )
