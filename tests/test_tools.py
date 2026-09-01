import pytest

from app.tools.base import Tool
from app.tools.registry import ToolRegistry, tool_registry


def test_tool_executes_handler():
    def add(a: int, b: int) -> int:
        return a + b

    tool = Tool(
        name="calculator",
        description="Add two numbers.",
        handler=add,
    )

    assert tool.execute(a=2, b=3) == 5


def test_registry_registers_and_returns_tool():
    registry = ToolRegistry()

    tool = Tool(
        name="example",
        description="Example tool",
        handler=lambda: "success",
    )

    registry.register(tool)

    assert registry.get("example") is tool


def test_registry_rejects_unknown_tool():
    registry = ToolRegistry()

    with pytest.raises(KeyError):
        registry.get("missing")


def test_global_registry_contains_knowledge_search():
    tool = tool_registry.get("search_knowledge_base")

    assert tool.name == "search_knowledge_base"


from app.observability.tracing import ExecutionTrace


def test_tool_execute_records_trace():
    trace = ExecutionTrace(request_id="req-tool")

    tool = Tool(
        name="calculator",
        description="Add numbers",
        handler=lambda a, b: a + b,
    )

    result = tool.execute(
        trace=trace,
        a=4,
        b=5,
    )

    assert result == 9
    assert trace.tool_call_count == 1
    assert trace.tool_success_count == 1
    assert trace.tool_calls[0].tool_name == "calculator"
