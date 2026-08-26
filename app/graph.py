from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.agents.planner import planner_agent
from app.agents.researcher import researcher_agent
from app.agents.retriever import retriever_agent
from app.agents.reviewer import finalize_review, reviewer_agent
from app.agents.writer import writer_agent


class AgentState(TypedDict, total=False):
    query: str
    plan: list[str]
    retrieved_context: list[dict]
    research_notes: str
    draft: str
    review: str
    answer: str


def planner_node(state: AgentState) -> AgentState:
    return {
        "plan": planner_agent(
            state["query"]
        )
    }


def retriever_node(state: AgentState) -> AgentState:
    return {
        "retrieved_context": retriever_agent(
            state["query"]
        )
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


def build_graph():
    workflow = StateGraph(AgentState)

    workflow.add_node(
        "planner",
        planner_node,
    )

    workflow.add_node(
        "retriever",
        retriever_node,
    )

    workflow.add_node(
        "researcher",
        researcher_node,
    )

    workflow.add_node(
        "writer",
        writer_node,
    )

    workflow.add_node(
        "reviewer",
        reviewer_node,
    )

    workflow.add_node(
        "finalize",
        finalize_node,
    )

    workflow.add_edge(
        START,
        "planner",
    )

    workflow.add_edge(
        "planner",
        "retriever",
    )

    workflow.add_edge(
        "retriever",
        "researcher",
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
