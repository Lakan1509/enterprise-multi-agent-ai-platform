from app.llm import LLMClient


def reviewer_agent(
    query: str,
    retrieved_context: list[dict],
    draft: str,
) -> str:
    """
    Review the draft for grounding, citation correctness, and completeness.
    """

    allowed_context = "\n\n".join(
        f"[{item['document_id']}:{item['chunk_id']}] {item['text']}"
        for item in retrieved_context
    )

    prompt = f"""
Question:
{query}

Allowed source context:
{allowed_context or "No source context available."}

Draft answer:
{draft}

Review the draft strictly.

Check:
1. Every factual claim is supported by the allowed source context.
2. Every factual claim has a valid citation.
3. No outside knowledge or generic advice was added.
4. The answer directly addresses the question.

Return exactly one of these formats:

PASS
The answer is fully grounded.

OR

REVISE
Reason: <brief reason>
Corrected answer:
<fully grounded corrected answer>
"""

    return LLMClient().complete(
        "You are a strict grounding and citation reviewer.",
        prompt,
    )


def finalize_review(review: str, draft: str) -> str:
    """
    Return the corrected answer when the reviewer requests revision.
    Otherwise return the original draft.
    """

    normalized_review = review.strip()

    if normalized_review.upper().startswith("REVISE"):
        marker = "Corrected answer:"

        if marker in normalized_review:
            corrected_answer = normalized_review.split(marker, 1)[1].strip()

            if corrected_answer:
                return corrected_answer

    return draft.strip()
