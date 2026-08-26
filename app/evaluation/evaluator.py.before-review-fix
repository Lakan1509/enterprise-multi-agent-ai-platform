from app.evaluation.schemas import EvaluationResult


def evaluate_run(
    *,
    answer: str,
    review: str,
    retry_count: int,
    latency_ms: float,
    tool_calls: int,
    route: str = "retrieval",
) -> EvaluationResult:
    """
    Evaluate a completed agent run using route-aware runtime signals.

    Retrieval responses require grounding review.
    Direct responses are evaluated without enterprise grounding requirements.
    """

    normalized_review = review.strip().upper()
    has_answer = bool(answer.strip())

    if route == "direct":
        grounded = True
        hallucination_detected = False
        task_success = has_answer
    else:
        grounded = normalized_review.startswith("PASS")
        hallucination_detected = (
            "UNSUPPORTED" in normalized_review
            or "HALLUCIN" in normalized_review
        )
        task_success = has_answer and grounded

    return EvaluationResult(
        task_success=task_success,
        grounded=grounded,
        hallucination_detected=hallucination_detected,
        retry_count=retry_count,
        latency_ms=latency_ms,
        tool_calls=tool_calls,
    )
