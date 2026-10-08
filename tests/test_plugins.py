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


# --- RagQueryTool ------------------------------------------------------------


class _FakeResp:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self):
        import json

        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_urlopen_factory(captured, payload):
    def _fake(req, timeout=None):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["api_key"] = req.get_header("X-api-key")
        return _FakeResp(payload)

    return _fake


def test_rag_tool_disabled_without_url(monkeypatch):
    from casi.integrations import RagQueryTool

    monkeypatch.delenv("CASI_RAG_API_URL", raising=False)
    tool = RagQueryTool()
    assert not tool.enabled
    with pytest.raises(RuntimeError, match="disabled"):
        tool.execute("health", {})


def test_rag_tool_query(monkeypatch):
    import urllib.request

    from casi.integrations import RagQueryTool

    captured = {}
    payload = {"answer": "Paris", "citations": [{"source": "doc1"}], "latency_s": 0.5}
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_factory(captured, payload))
    tool = RagQueryTool(base_url="http://127.0.0.1:8001", api_key="sekret")
    result = tool.execute("query", {"query": "capital of France?"})
    assert result["answer"] == "Paris"
    assert result["citations"] == [{"source": "doc1"}]
    assert captured["url"] == "http://127.0.0.1:8001/query"
    assert captured["method"] == "POST"
    assert captured["api_key"] == "sekret"


def test_rag_tool_query_requires_query():
    from casi.integrations import RagQueryTool

    tool = RagQueryTool(base_url="http://127.0.0.1:8001")
    with pytest.raises(ValueError, match="non-empty 'query'"):
        tool.execute("query", {"query": "   "})
    with pytest.raises(ValueError, match="unknown action"):
        tool.execute("nope", {})


def test_rag_tool_registers_in_registry():
    from casi.integrations import PluginRegistry, RagQueryTool

    reg = PluginRegistry()
    reg.register(RagQueryTool(base_url="http://127.0.0.1:8001"))
    assert "rag_query" in reg.list()


def test_rag_tool_rejects_non_http_scheme():
    from casi.integrations import RagQueryTool

    tool = RagQueryTool(base_url="file:///etc/passwd")
    with pytest.raises(ValueError, match="non-http"):
        tool.execute("health", {})
