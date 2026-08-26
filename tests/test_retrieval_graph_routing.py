from app.graph import route_after_retrieval


def test_fast_retrieval_mode_routes_to_fast_answer():
    state = {
        "retrieval_mode": "fast",
    }

    assert route_after_retrieval(state) == "fast"


def test_research_mode_routes_to_researcher():
    state = {
        "retrieval_mode": "research",
    }

    assert route_after_retrieval(state) == "research"


def test_missing_mode_defaults_to_fast():
    state = {}

    assert route_after_retrieval(state) == "fast"
