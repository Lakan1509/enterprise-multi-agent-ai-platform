"""CASI integrations: plugin interface, registry, future-phase stubs, and the optional RAG query tool."""

from .plugins import (
    MultimodalPlugin,
    Plugin,
    PluginRegistry,
    RagQueryTool,
    RoboticsPlugin,
)

__all__ = ["Plugin", "PluginRegistry", "MultimodalPlugin", "RoboticsPlugin", "RagQueryTool"]
