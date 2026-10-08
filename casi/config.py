"""Configuration for CASI.

``Settings`` is a pydantic-settings model: every field is optional and can be
overridden from the environment with the ``CASI_`` prefix (e.g.
``CASI_DATA_DIR``, ``CASI_MAX_WORKERS``).
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the CASI kernel and components."""

    model_config = SettingsConfigDict(env_prefix="CASI_", extra="ignore")

    api_key: str = ""
    """Shared API key; when set, the API layer enforces auth. Empty disables auth."""

    data_dir: Path = Path("./.casi-data")
    """Root directory for all CASI persisted state (memory, audit, runs, workspace)."""

    workspace_dir: Path | None = None
    """Agent workspace root. Defaults to ``data_dir / "workspace"`` when unset."""

    max_workers: int = 4
    """Maximum parallel task workers in the DAG scheduler."""

    default_provider: str = "mock"
    """Name of the default LLM provider in the model router."""

    ollama_host: str = "http://localhost:11434"
    """Base URL for the optional Ollama provider."""

    ollama_model: str = "qwen2.5:7b"
    """Default model name for the optional Ollama provider."""

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
