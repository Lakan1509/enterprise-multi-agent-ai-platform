from app.evaluation.schemas import EvaluationResult


def evaluate_run(
    *,
    answer: str,
    review: str,
    retry_count: int,
    latency_ms: float,
    tool_calls: int,
) -> EvaluationResult:
    """
    Evaluate one completed agent run using deterministic runtime signals.
    """

    normalized_review = review.strip().upper()

    grounded = normalized_review.startswith("PASS")
    hallucination_detected = "UNSUPPORTED" in normalized_review

    task_success = bool(answer.strip()) and grounded

    return EvaluationResult(
        task_success=task_success,
        grounded=grounded,
        hallucination_detected=hallucination_detected,
        retry_count=retry_count,
        latency_ms=latency_ms,
        tool_calls=tool_calls,
    )
