from app.graph import route_after_review


def test_pass_goes_to_finalize():
    state = {
        "review": "PASS\nThe answer is fully grounded.",
        "retry_count": 0,
    }

    assert route_after_review(state) == "finalize"


def test_revise_goes_to_rewrite():
    state = {
        "review": "REVISE\nReason: Unsupported claim.",
        "retry_count": 0,
    }

    assert route_after_review(state) == "rewrite"


def test_second_retry_is_allowed():
    state = {
        "review": "REVISE\nReason: Citation problem.",
        "retry_count": 1,
    }

    assert route_after_review(state) == "rewrite"


def test_retry_limit_forces_finalize():
    state = {
        "review": "REVISE\nReason: Still incorrect.",
        "retry_count": 2,
    }

    assert route_after_review(state) == "finalize"


def test_missing_review_fails_safely_to_finalize():
    state = {
        "retry_count": 0,
    }

    assert route_after_review(state) == "finalize"
