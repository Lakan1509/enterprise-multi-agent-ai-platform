from typing import Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.agents.planner import planner_agent
from app.agents.researcher import researcher_agent
from app.agents.reviewer import finalize_review, reviewer_agent
from app.agents.supervisor import supervisor_agent
from app.agents.writer import writer_agent
from app.tools.registry import tool_registry


class AgentState(TypedDict, total=False):
    query: str
    plan: list[str]
    route: str
    retrieved_context: list[dict]
    research_notes: str
    draft: str
    review: str
    answer: str


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
    )

    return {
        "retrieved_context": results
    }


def direct_node(state: AgentState) -> AgentState:
    return {
        "retrieved_context": [],
        "research_notes": (
            "This request does not require enterprise document retrieval."
        ),
    }


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
    return {
        "review": reviewer_agent(
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
    }


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
    workflow.add_node("researcher", researcher_node)
    workflow.add_node("writer", writer_node)
    workflow.add_node("reviewer", reviewer_node)
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

    workflow.add_edge(
        "retrieval",
        "researcher",
    )

    workflow.add_edge(
        "direct",
        "writer",
    )

    workflow.add_edge(
        "researcher",
        "writer",
    )

    workflow.add_edge(
        "writer",
        "reviewer",
    )

    workflow.add_edge(
        "reviewer",
        "finalize",
    )

    workflow.add_edge(
        "finalize",
        END,
    )

    return workflow.compile(
        checkpointer=InMemorySaver()
    )


graph = build_graph()
