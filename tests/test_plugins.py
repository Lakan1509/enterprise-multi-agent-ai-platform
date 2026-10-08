"""Tests for casi.integrations.plugins."""

import pytest

from casi.integrations import (
    MultimodalPlugin,
    Plugin,
    PluginRegistry,
    RoboticsPlugin,
)


class _EchoPlugin(Plugin):
    name = "echo"
    description = "test echo plugin"

    def execute(self, action: str, params: dict) -> dict:
        return {"action": action, "params": params}


def test_register_list_execute() -> None:
    registry = PluginRegistry()
    registry.register(_EchoPlugin())
    assert registry.list() == ["echo"]
    assert registry.get("echo").name == "echo"
    assert registry.execute("echo", "ping", {"a": 1}) == {
        "action": "ping",
        "params": {"a": 1},
    }


def test_duplicate_register_raises() -> None:
    registry = PluginRegistry()
    registry.register(_EchoPlugin())
    with pytest.raises(ValueError, match="already registered"):
        registry.register(_EchoPlugin())


def test_get_missing_raises_key_error() -> None:
    registry = PluginRegistry()
    with pytest.raises(KeyError):
        registry.get("nope")
    with pytest.raises(KeyError):
        registry.execute("nope", "x", {})


@pytest.mark.parametrize(
    "plugin_cls, phase",
    [(MultimodalPlugin, "Phase-7"), (RoboticsPlugin, "Phase-8")],
)
def test_stubs_raise_not_implemented(plugin_cls, phase: str) -> None:
    plugin = plugin_cls()
    assert plugin.name in ("multimodal", "robotics")
    with pytest.raises(NotImplementedError, match=phase):
        plugin.execute("anything", {})
