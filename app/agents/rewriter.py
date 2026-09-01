from app.llm import LLMClient


def rewriter_agent(
    query: str,
    draft: str,
    review: str,
    retrieved_context: list[dict],
) -> str:
    """
    Rewrite a rejected answer using reviewer feedback and allowed context.
    """

    context = "\n\n".join(
        f"[{item['document_id']}:{item['chunk_id']}] {item['text']}"
        for item in retrieved_context
    )

    prompt = f"""
Original user question:
{query}

Allowed source context:
{context or "No enterprise source context is available."}

Previous draft:
{draft}

Reviewer feedback:
{review}

Rewrite the answer.

Rules:
- Correct the problems identified by the reviewer.
- Use only the allowed source context for enterprise-specific facts.
- Do not invent facts.
- Do not fabricate citations.
- Preserve valid citations in [document_id:chunk_id] format.
- Answer the user's question directly.
"""

    return LLMClient().complete(
        "You are the correction agent in a production AI platform.",
        prompt,
    )
