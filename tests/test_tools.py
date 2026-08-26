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
