from app.llm import LLMClient


def researcher_agent(
    query: str,
    retrieved_context: list[dict],
) -> str:
    """
    Convert retrieved enterprise context into grounded research notes.

    Every factual statement should preserve citations in the exact form:
    [document_id:chunk_id]
    """

    context = "\n\n".join(
        f"[{item['document_id']}:{item['chunk_id']}] {item['text']}"
        for item in retrieved_context
    )

    if not context:
        return (
            "No relevant internal document context was found. "
            "The available documents do not contain enough information "
            "to answer the question."
        )

    prompt = f"""
Question:
{query}

Retrieved internal document context:
{context}

Create concise research notes using ONLY the retrieved context.

Rules:
- Do not use outside knowledge.
- Do not invent facts, requirements, or recommendations.
- Preserve exact facts, numbers, and conditions.
- Attach the exact citation [document_id:chunk_id] to every factual statement.
- If part of the question cannot be answered from the context, state that clearly.
"""

    return LLMClient().complete(
        (
            "You are a strict enterprise document research agent. "
            "Use only the supplied internal context."
        ),
        prompt,
    )
