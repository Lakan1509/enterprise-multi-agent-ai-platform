from app.graph import route_from_supervisor


def test_route_from_supervisor_returns_retrieval():
    state = {"route": "retrieval"}

    assert route_from_supervisor(state) == "retrieval"


def test_route_from_supervisor_returns_direct():
    state = {"route": "direct"}

    assert route_from_supervisor(state) == "direct"


def test_route_from_supervisor_defaults_to_retrieval():
    state = {}

    assert route_from_supervisor(state) == "retrieval"
