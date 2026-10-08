"""CASI integrations: plugin interface, registry, and future-phase stubs."""

from .plugins import (
    MultimodalPlugin,
    Plugin,
    PluginRegistry,
    RoboticsPlugin,
)

__all__ = ["Plugin", "PluginRegistry", "MultimodalPlugin", "RoboticsPlugin"]
