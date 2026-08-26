from app.llm import LLMClient


def writer_agent(
    query: str,
    research_notes: str,
) -> str:
    """
    Generate a grounded answer using only the research notes.
    """

    prompt = f"""
Question:
{query}

Research notes:
{research_notes}

Write a direct answer using ONLY the research notes.

Rules:
- Do not add outside knowledge.
- Do not invent facts.
- Do not add generic advice.
- Keep citations in the exact format [document_id:chunk_id].
- Every factual statement must have a citation.
- If the research notes do not contain enough information, say so clearly.
- Use concise paragraphs or bullet points.
"""

    return LLMClient().complete(
        "You are a grounded enterprise RAG response writer.",
        prompt,
    )
