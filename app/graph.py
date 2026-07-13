from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.config import get_settings
from app.llm import LLMClient
from app.vector_store import FaissStore


class AgentState(TypedDict, total=False):
    query: str
    plan: list[str]
    retrieved_context: list[dict]
    research_notes: str
    draft: str
    review: str
    answer: str


def planner_agent(state: AgentState) -> AgentState:
    prompt = f"""
Create a concise 3-5 step plan for answering this enterprise knowledge question.

Question:
{state["query"]}

Return one step per line without numbering.
"""

    raw = LLMClient().complete(
        "You are a planning agent for an enterprise AI assistant.",
        prompt,
    )

    plan = [
        line.strip(" -•\t")
        for line in raw.splitlines()
        if line.strip()
    ]

    return {"plan": plan[:5]}


def retriever_agent(state: AgentState) -> AgentState:
    settings = get_settings()

    results = FaissStore().search(
        state["query"],
        top_k=settings.top_k,
    )

    return {"retrieved_context": results}


def researcher_agent(state: AgentState) -> AgentState:
    retrieved_items = state.get("retrieved_context", [])

    context = "\n\n".join(
        f"[{item['document_id']}:{item['chunk_id']}] {item['text']}"
        for item in retrieved_items
    )

    if not context:
        return {
            "research_notes": (
                "No relevant internal document context was found. "
                "The available documents do not contain enough information "
                "to answer the question."
            )
        }

    prompt = f"""
Question:
{state["query"]}

Retrieved internal document context:
{context}

Create concise research notes using ONLY the retrieved context.

Rules:
- Do not use outside knowledge.
- Do not invent requirements or recommendations.
- Preserve exact facts, numbers, and conditions.
- Attach the exact citation [document_id:chunk_id] to every factual statement.
- If the context does not answer part of the question, state that clearly.
"""

    notes = LLMClient().complete(
        "You are a strict enterprise document research agent. "
        "Use only the supplied internal context.",
        prompt,
    )

    return {"research_notes": notes}


def writer_agent(state: AgentState) -> AgentState:
    prompt = f"""
Question:
{state["query"]}

Research notes:
{state.get("research_notes", "")}

Write a direct answer using ONLY the research notes.

Rules:
- Do not add outside knowledge.
- Do not add generic advice.
- Do not invent facts.
- Keep citations in the exact format [document_id:chunk_id].
- Every factual statement must have a citation.
- If the documents do not contain enough information, say so clearly.
- Use short paragraphs or bullet points.
"""

    draft = LLMClient().complete(
        "You are a grounded enterprise RAG response writer.",
        prompt,
    )

    return {"draft": draft}


def reviewer_agent(state: AgentState) -> AgentState:
    retrieved_items = state.get("retrieved_context", [])

    allowed_context = "\n\n".join(
        f"[{item['document_id']}:{item['chunk_id']}] {item['text']}"
        for item in retrieved_items
    )

    prompt = f"""
Question:
{state["query"]}

Allowed source context:
{allowed_context or "No source context available."}

Draft answer:
{state.get("draft", "")}

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

    review = LLMClient().complete(
        "You are a strict grounding and citation reviewer.",
        prompt,
    )

    return {"review": review}


def finalize_agent(state: AgentState) -> AgentState:
    review = state.get("review", "").strip()
    draft = state.get("draft", "").strip()

    if review.upper().startswith("REVISE"):
        marker = "Corrected answer:"

        if marker in review:
            corrected_answer = review.split(marker, 1)[1].strip()

            if corrected_answer:
                return {"answer": corrected_answer}

    return {"answer": draft}


def build_graph():
    workflow = StateGraph(AgentState)

    workflow.add_node("planner", planner_agent)
    workflow.add_node("retriever", retriever_agent)
    workflow.add_node("researcher", researcher_agent)
    workflow.add_node("writer", writer_agent)
    workflow.add_node("reviewer", reviewer_agent)
    workflow.add_node("finalize", finalize_agent)

    workflow.add_edge(START, "planner")
    workflow.add_edge("planner", "retriever")
    workflow.add_edge("retriever", "researcher")
    workflow.add_edge("researcher", "writer")
    workflow.add_edge("writer", "reviewer")
    workflow.add_edge("reviewer", "finalize")
    workflow.add_edge("finalize", END)

    return workflow.compile(checkpointer=InMemorySaver())


graph = build_graph()