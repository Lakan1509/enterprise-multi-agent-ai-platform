from app.llm import LLMClient


def grounded_answer_agent(
    query: str,
    retrieved_context: list[dict],
) -> str:
    """
    Answer a simple enterprise lookup directly from retrieved context.

    This fast path avoids the researcher -> writer chain while preserving
    grounding and citations.
    """

    if not retrieved_context:
        return (
            "I could not find enough information in the internal "
            "knowledge base to answer this question."
        )

    context = "\n\n".join(
        f"[{item['document_id']}:{item['chunk_id']}] {item['text']}"
        for item in retrieved_context
    )

    prompt = f"""
Question:
{query}

Allowed enterprise context:
{context}

Answer the question directly using ONLY the allowed enterprise context.

Rules:
- Use only facts necessary to answer the question.
- Do not include unrelated facts from the retrieved chunk.
- Do not use outside knowledge.
- Do not invent requirements.
- Every factual claim must include its citation.
- Preserve citations exactly as [document_id:chunk_id].
- Prefer a concise answer.
- If the context does not contain the answer, say so clearly.

Return only the final answer.
"""

    return LLMClient().complete(
        (
            "You are a precise enterprise knowledge-base answering agent. "
            "Use only supplied evidence and answer only what was asked."
        ),
        prompt,
    )
