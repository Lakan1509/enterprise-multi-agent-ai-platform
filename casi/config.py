"""Configuration for CASI.

``Settings`` is a pydantic-settings model: every field is optional and can be
overridden from the environment with the ``CASI_`` prefix (e.g.
``CASI_DATA_DIR``, ``CASI_MAX_WORKERS``).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)

#: Minimum accepted length for the API key in an auth-required deployment.
MIN_API_KEY_LENGTH = 32

#: Values that are clearly placeholders, not real secrets (case-insensitive).
_PLACEHOLDER_KEY_VALUES = frozenset(
    {
        "changeme",
        "change-me",
        "change_me",
        "password",
        "secret",
        "test",
        "testing",
        "demo",
        "example",
        "apikey",
        "api_key",
        "api-key",
        "your-api-key",
        "your_api_key",
        "your-api-key-here",
        "placeholder",
        "key",
        "123456",
        "xxxxxxxx",
    }
)

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class InsecureDeploymentError(Exception):
    """Raised at startup when the deployment requires auth but is misconfigured."""


def is_loopback_host(host: str) -> bool:
    """Return True when ``host`` is a loopback-only bind address.

    ``0.0.0.0`` (all interfaces) is deliberately *not* loopback: it exposes
    the server beyond the local machine, so it fails the loopback test and
    triggers the auth requirement.
    """
    h = (host or "").strip().lower()
    if h in _LOOPBACK_HOSTS:
        return True
    # 127.0.0.0/8 — the full IPv4 loopback range.
    return h.startswith("127.")


def looks_like_placeholder(value: str) -> bool:
    """Return True when ``value`` smells like a placeholder, not a real key."""
    v = (value or "").strip().lower()
    if not v:
        return True
    if v in _PLACEHOLDER_KEY_VALUES:
        return True
    if "changeme" in v or "placeholder" in v or "example" in v:
        return True
    # a single repeated character is not a secret, however long
    return len(set(v)) == 1


class Settings(BaseSettings):
    """Runtime configuration for the CASI kernel and components."""

    model_config = SettingsConfigDict(env_prefix="CASI_", extra="ignore")

    api_key: str = ""
    """Admin API key (``X-API-Key``); when set, the API layer enforces auth.
    Empty disables auth — only acceptable for loopback dev use."""

    operator_api_key: str = ""
    """Optional API key that authenticates as :data:`Role.OPERATOR`."""

    viewer_api_key: str = ""
    """Optional API key that authenticates as :data:`Role.VIEWER` (read-only)."""

    require_auth: bool = False
    """When true, auth is mandatory even on loopback: startup fails fast
    unless a strong ``api_key`` is configured."""

    host: str = "127.0.0.1"
    """Bind address for the API server. Anything non-loopback requires auth."""

    port: int = 8000
    """Port for the API server."""

    data_dir: Path = Path("./.casi-data")
    """Root directory for all CASI persisted state (memory, audit, runs, workspace)."""

    workspace_dir: Path | None = None
    """Agent workspace root. Defaults to ``data_dir / "workspace"`` when unset."""

    max_workers: int = 4
    """Maximum parallel task workers in the DAG scheduler."""

    default_provider: str = "mock"
    """Name of the default LLM provider in the model router.

    Note: the Ollama/Nebius providers read their own env vars directly
    (``OLLAMA_HOST``/``OLLAMA_MODEL``, ``NEBIUS_API_KEY``/``NEBIUS_MODEL``/
    ``NEBIUS_BASE_URL``); there are no per-provider fields here on purpose.
    """

    request_timeout_s: int = 60
    """Default timeout (seconds) for outbound requests / tool calls."""

    @property
    def resolved_workspace_dir(self) -> Path:
        """Workspace root: explicit override or ``data_dir / "workspace"``."""
        return self.workspace_dir if self.workspace_dir is not None else self.data_dir / "workspace"

    @property
    def runs_dir(self) -> Path:
        """Directory holding per-goal run state (checkpoints, reports)."""
        return self.data_dir / "runs"

    @property
    def memory_path(self) -> Path:
        """JSONL file backing long-term memory."""
        return self.data_dir / "memory.jsonl"

    @property
    def audit_path(self) -> Path:
        """JSONL file backing the audit log."""
        return self.data_dir / "audit.jsonl"

    def ensure_dirs(self) -> None:
        """Create the data, workspace, and runs directories if missing."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.resolved_workspace_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    # -- deployment security ----------------------------------------------

    def _reject_insecure(self, why: str) -> None:
        raise InsecureDeploymentError(
            "Refusing to start: this deployment requires API authentication but "
            f"{why}. Set a strong CASI_API_KEY (>= {MIN_API_KEY_LENGTH} chars, "
            "not a placeholder) before exposing CASI beyond loopback, or set "
            "CASI_REQUIRE_AUTH=0 and bind to 127.0.0.1 for local dev only."
        )

    def validate_deployment(self) -> None:
        """Fail fast on insecure deployments; warn loudly on unauthenticated dev.

        Rules (fail closed):

        * If the bind address is non-loopback (``host`` not 127.0.0.1/::1/
          localhost) **or** ``CASI_REQUIRE_AUTH=1``, startup raises
          :class:`InsecureDeploymentError` unless ``api_key`` is a strong
          value (>= ``MIN_API_KEY_LENGTH`` chars, not a placeholder).
        * Otherwise (loopback dev without a key) startup proceeds but emits
          a loud warning (``logging.warning`` + ``print``) that auth is
          disabled. A weak/placeholder key on loopback warns loudly too.

        Call this once at application startup.
        """
        auth_required = self.require_auth or not is_loopback_host(self.host)
        if auth_required:
            if not self.api_key:
                self._reject_insecure("CASI_API_KEY is not set")
            if len(self.api_key) < MIN_API_KEY_LENGTH:
                self._reject_insecure(
                    f"CASI_API_KEY is too short ({len(self.api_key)} chars, "
                    f"minimum {MIN_API_KEY_LENGTH})"
                )
            if looks_like_placeholder(self.api_key):
                self._reject_insecure("CASI_API_KEY looks like a placeholder value")
            return

        # Loopback dev: usable without a key, but say so loudly.
        if not self.api_key:
            banner = (
                "\n"
                "!!! CASI SECURITY WARNING: API authentication is DISABLED !!!\n"
                "No CASI_API_KEY is configured and require_auth is off.\n"
                "This server is only safe bound to loopback (127.0.0.1/::1).\n"
                "Never expose it beyond localhost without setting a strong CASI_API_KEY."
            )
            log.warning("CASI auth disabled: no CASI_API_KEY configured (loopback dev mode).")
            print(f"WARNING: {banner}", file=sys.stderr)
        elif len(self.api_key) < MIN_API_KEY_LENGTH or looks_like_placeholder(self.api_key):
            banner = (
                "\n"
                "!!! CASI SECURITY WARNING: the configured CASI_API_KEY is weak !!!\n"
                f"It must be >= {MIN_API_KEY_LENGTH} characters and not a placeholder "
                "before any non-loopback deployment."
            )
            log.warning("CASI_API_KEY is weak (short or placeholder-like).")
            print(f"WARNING: {banner}", file=sys.stderr)
