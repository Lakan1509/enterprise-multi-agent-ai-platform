from app.tools.base import Tool
from app.tools.retrieval import knowledge_search_tool


class ToolRegistry:
    """
    Central registry for tools available to AI agents.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")

        return self._tools[name]

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())


tool_registry = ToolRegistry()
tool_registry.register(knowledge_search_tool)
