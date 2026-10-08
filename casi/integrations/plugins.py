"""Plugin interface and registry for CASI integrations.

:class:`Plugin` is the abstract base every integration implements;
:class:`PluginRegistry` manages named plugins and dispatches ``execute``.
:class:`MultimodalPlugin` and :class:`RoboticsPlugin` are explicit stubs for
future phases — their ``execute`` raises :class:`NotImplementedError`.
:class:`RagQueryTool` is a thin optional HTTP client for the pre-existing
base-repo RAG application (``POST /query``); it is disabled unless
``CASI_RAG_API_URL`` is set.
"""

from __future__ import annotations

import abc
import os
import urllib.request
import urllib.error
import json


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


class RagQueryTool(Plugin):
    """Thin optional client for the base-repo RAG application.

    The pre-existing ``enterprise-multi-agent-ai-platform`` app exposes
    ``GET /health`` and ``POST /query`` (``{"query", "thread_id"}``). This
    tool lets CASI agents consult it as a grounded Q&A source. It performs
    no imports of the base repo — the seam is plain HTTP, so there is no
    import-time coupling and no file collision.

    Configuration (env only):
    - ``CASI_RAG_API_URL``: base URL, e.g. ``http://127.0.0.1:8001``.
      Unset/empty → the tool is disabled and ``execute`` raises
      :class:`RuntimeError`.
    - ``CASI_RAG_API_KEY``: optional API key sent as ``X-API-Key``.

    Actions:
    - ``"health"`` → ``{"ok": bool, "status": ...}``
    - ``"query"`` with ``params={"query": str, "thread_id": str|None}``
      → ``{"answer": str, "citations": [...], "latency_s": float}``
    """

    name = "rag_query"
    description = (
        "Optional HTTP client for the base-repo RAG Q&A app "
        "(POST /query). Disabled unless CASI_RAG_API_URL is set."
    )

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 timeout_s: float = 30.0) -> None:
        self._base_url = (base_url if base_url is not None
                          else os.environ.get("CASI_RAG_API_URL", "")).rstrip("/")
        self._api_key = (api_key if api_key is not None
                         else os.environ.get("CASI_RAG_API_KEY", ""))
        self._timeout_s = timeout_s

    @property
    def enabled(self) -> bool:
        """Whether the tool is configured (base URL set)."""
        return bool(self._base_url)

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        if not self.enabled:
            raise RuntimeError(
                "rag_query is disabled: set CASI_RAG_API_URL to enable it"
            )
        url = self._base_url + path
        scheme = url.split("://", 1)[0].lower()
        if scheme not in ("http", "https"):
            raise ValueError(
                f"refusing non-http(s) RAG API URL scheme: {scheme!r}"
            )
        data = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data if method == "POST" else None,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        if self._api_key:
            req.add_header("X-API-Key", self._api_key)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"rag api http {exc.code}: {exc.reason}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"rag api unreachable: {exc.reason}") from exc

    def execute(self, action: str, params: dict) -> dict:
        """Execute ``"health"`` or ``"query"`` against the RAG API."""
        if action == "health":
            body = self._request("GET", "/health")
            return {"ok": True, "status": body}
        if action == "query":
            query = (params or {}).get("query", "")
            if not query or not str(query).strip():
                raise ValueError("query action requires a non-empty 'query' param")
            body = self._request("POST", "/query", {
                "query": str(query),
                "thread_id": str((params or {}).get("thread_id") or "casi"),
            })
            return {
                "answer": body.get("answer", ""),
                "citations": body.get("citations", []),
                "latency_s": body.get("latency_s", body.get("elapsed_s", 0.0)),
                "raw": body,
            }
        raise ValueError(f"unknown action {action!r} (expected 'health' or 'query')")
