from typing import Literal, TypedDict

from app.observability.tracing import ExecutionTrace

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.agents.direct import direct_agent
from app.agents.direct_reviewer import direct_reviewer_agent
from app.agents.direct_rewriter import direct_rewriter_agent
from app.agents.planner import planner_agent
from app.agents.grounded_answer import grounded_answer_agent, normalize_citations
from app.agents.researcher import researcher_agent
from app.agents.retrieval_router import retrieval_router
from app.agents.reviewer import finalize_review, reviewer_agent
from app.agents.rewriter import rewriter_agent
from app.agents.supervisor import supervisor_agent
from app.agents.writer import writer_agent
from app.tools.registry import tool_registry


class AgentState(TypedDict, total=False):
    query: str
    trace: ExecutionTrace
    plan: list[str]
    route: str
    retrieved_context: list[dict]
    retrieval_mode: str
    research_notes: str
    draft: str
    review: str
    answer: str
    retry_count: int


def planner_node(state: AgentState) -> AgentState:
    return {
        "plan": planner_agent(state["query"])
    }


def supervisor_node(state: AgentState) -> AgentState:
    return {
        "route": supervisor_agent(state["query"])
    }


def retrieval_node(state: AgentState) -> AgentState:
    tool = tool_registry.get("search_knowledge_base")

    results = tool.execute(
        query=state["query"],
        trace=state.get("trace"),
    )

    return {
        "retrieved_context": results,
        "retrieval_mode": retrieval_router(
            state["query"]
        ),
    }


def direct_node(state: AgentState) -> AgentState:
    return {
        "draft": direct_agent(
            state["query"]
        ),
        "retrieved_context": [],
    }



def fast_answer_node(state: AgentState) -> AgentState:
    retrieved_context = state.get(
        "retrieved_context",
        [],
    )

    draft = grounded_answer_agent(
        query=state["query"],
        retrieved_context=retrieved_context,
    )

    draft = normalize_citations(
        draft,
        retrieved_context,
    )

    return {
        "draft": draft,
        "review": (
            "PASS\n"
            "Fast retrieval answer was produced directly "
            "from retrieved source evidence."
        ),
    }


def route_after_retrieval(
    state: AgentState,
) -> Literal["fast", "research"]:
    if state.get("retrieval_mode") == "research":
        return "research"

    return "fast"


def researcher_node(state: AgentState) -> AgentState:
    return {
        "research_notes": researcher_agent(
            query=state["query"],
            retrieved_context=state.get(
                "retrieved_context",
                [],
            ),
        )
    }


def writer_node(state: AgentState) -> AgentState:
    return {
        "draft": writer_agent(
            query=state["query"],
            research_notes=state.get(
                "research_notes",
                "",
            ),
        )
    }


def reviewer_node(state: AgentState) -> AgentState:
    if state.get("route") == "direct":
        review = direct_reviewer_agent(
            query=state["query"],
            draft=state.get("draft", ""),
        )
    else:
        review = reviewer_agent(
            query=state["query"],
            retrieved_context=state.get(
                "retrieved_context",
                [],
            ),
            draft=state.get(
                "draft",
                "",
            ),
        )

    return {
        "review": review
    }



def rewriter_node(state: AgentState) -> AgentState:
    retry_count = state.get("retry_count", 0)

    if state.get("route") == "direct":
        rewritten = direct_rewriter_agent(
            query=state["query"],
            draft=state.get("draft", ""),
            review=state.get("review", ""),
        )
    else:
        rewritten = rewriter_agent(
            query=state["query"],
            draft=state.get("draft", ""),
            review=state.get("review", ""),
            retrieved_context=state.get(
                "retrieved_context",
                [],
            ),
        )

    return {
        "draft": rewritten,
        "retry_count": retry_count + 1,
    }


def route_after_review(
    state: AgentState,
) -> Literal["rewrite", "finalize"]:
    review = state.get("review", "").strip()
    normalized = review.upper()
    retry_count = state.get("retry_count", 0)

    if normalized.startswith("PASS"):
        return "finalize"

    if (
        normalized.startswith("REVISE")
        and "Corrected answer:" in review
    ):
        return "finalize"

    if normalized.startswith("REVISE") and retry_count < 2:
        return "rewrite"

    return "finalize"


def finalize_node(state: AgentState) -> AgentState:
    return {
        "answer": finalize_review(
            review=state.get("review", ""),
            draft=state.get("draft", ""),
        )
    }


def route_from_supervisor(
    state: AgentState,
) -> Literal["retrieval", "direct"]:
    if state.get("route") == "direct":
        return "direct"

    return "retrieval"


def build_graph():
    workflow = StateGraph(AgentState)

    workflow.add_node("planner", planner_node)
    workflow.add_node("supervisor", supervisor_node)
    workflow.add_node("retrieval", retrieval_node)
    workflow.add_node("direct", direct_node)
    workflow.add_node("fast_answer", fast_answer_node)
    workflow.add_node("researcher", researcher_node)
    workflow.add_node("writer", writer_node)
    workflow.add_node("reviewer", reviewer_node)
    workflow.add_node("rewriter", rewriter_node)
    workflow.add_node("finalize", finalize_node)

    workflow.add_edge(
        START,
        "planner",
    )

    workflow.add_edge(
        "planner",
        "supervisor",
    )

    workflow.add_conditional_edges(
        "supervisor",
        route_from_supervisor,
        {
            "retrieval": "retrieval",
            "direct": "direct",
        },
    )

    workflow.add_conditional_edges(
        "retrieval",
        route_after_retrieval,
        {
            "fast": "fast_answer",
            "research": "researcher",
        },
    )

    workflow.add_edge(
        "fast_answer",
        "finalize",
    )

    workflow.add_edge(
        "direct",
        "finalize",
    )

    workflow.add_edge(
        "researcher",
        "writer",
    )

    workflow.add_edge(
        "writer",
        "reviewer",
    )

    workflow.add_conditional_edges(
        "reviewer",
        route_after_review,
        {
            "rewrite": "rewriter",
            "finalize": "finalize",
        },
    )

    workflow.add_edge(
        "rewriter",
        "reviewer",
    )

    workflow.add_edge(
        "finalize",
        END,
    )

    return workflow.compile(
        checkpointer=InMemorySaver()
    )


graph = build_graph()
