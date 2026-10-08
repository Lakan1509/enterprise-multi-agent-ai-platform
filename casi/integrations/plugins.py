"""Plugin interface and registry for CASI integrations.

:class:`Plugin` is the abstract base every integration implements;
:class:`PluginRegistry` manages named plugins and dispatches ``execute``.
:class:`MultimodalPlugin` and :class:`RoboticsPlugin` are explicit stubs for
future phases — their ``execute`` raises :class:`NotImplementedError`.
"""

from __future__ import annotations

import abc


class Plugin(abc.ABC):
    """Abstract integration plugin."""

    name: str
    """Unique plugin name used for registry lookup."""

    description: str = ""
    """Human-readable description of what the plugin does."""

    @abc.abstractmethod
    def execute(self, action: str, params: dict) -> dict:
        """Execute ``action`` with ``params``; return a result dict."""
        raise NotImplementedError


class PluginRegistry:
    """Named registry of :class:`Plugin` instances."""

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}

    def register(self, plugin: Plugin) -> None:
        """Register ``plugin`` under its ``name``.

        Raises:
            ValueError: If a plugin with the same name is already registered.
        """
        if plugin.name in self._plugins:
            raise ValueError(f"plugin already registered: {plugin.name!r}")
        self._plugins[plugin.name] = plugin

    def get(self, name: str) -> Plugin:
        """Return the plugin registered as ``name``.

        Raises:
            KeyError: If no plugin is registered under ``name``.
        """
        try:
            return self._plugins[name]
        except KeyError:
            raise KeyError(f"unknown plugin: {name!r}") from None

    def list(self) -> list[str]:
        """Sorted names of all registered plugins."""
        return sorted(self._plugins.keys())

    def execute(self, name: str, action: str, params: dict) -> dict:
        """Execute ``action`` on the plugin registered as ``name``.

        Raises:
            KeyError: If no plugin is registered under ``name``.
        """
        return self.get(name).execute(action, params)


class MultimodalPlugin(Plugin):
    """Stub for Phase-7 multimodal support (not implemented)."""

    name = "multimodal"
    description = "Phase-7 stub: multimodal (image/audio) support is not implemented."

    def execute(self, action: str, params: dict) -> dict:
        raise NotImplementedError("multimodal support is a Phase-7 stub")


class RoboticsPlugin(Plugin):
    """Stub for Phase-8 robotics support (not implemented)."""

    name = "robotics"
    description = "Phase-8 stub: robotics support is not implemented."

    def execute(self, action: str, params: dict) -> dict:
        raise NotImplementedError("robotics support is a Phase-8 stub")
