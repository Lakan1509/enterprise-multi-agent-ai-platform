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
    Evaluate one completed agent run.

    Important:
    Hallucination refers to unsupported content remaining in the FINAL
    answer, not issues that were detected and corrected during review.
    """

    normalized_review = review.strip().upper()
    has_answer = bool(answer.strip())

    if route == "direct":
        grounded = True
        hallucination_detected = False
        task_success = has_answer

    else:
        reviewer_passed = normalized_review.startswith("PASS")

        reviewer_corrected = (
            normalized_review.startswith("REVISE")
            and "CORRECTED ANSWER:" in normalized_review
        )

        grounded = reviewer_passed or reviewer_corrected

        # If the reviewer supplied a corrected final answer,
        # the detected defect belongs to the previous draft.
        if reviewer_corrected:
            hallucination_detected = False

        elif reviewer_passed:
            hallucination_detected = False

        else:
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
