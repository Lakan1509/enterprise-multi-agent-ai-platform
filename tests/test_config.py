"""Tests for casi.config.Settings."""

import os
from pathlib import Path

from casi.config import Settings


def test_defaults():
    s = Settings()
    assert s.api_key == ""
    assert s.data_dir == Path("./.casi-data")
    assert s.workspace_dir is None
    assert s.max_workers == 4
    assert s.default_provider == "mock"
    assert s.ollama_host == "http://localhost:11434"
    assert s.ollama_model == "qwen2.5:7b"
    assert s.request_timeout_s == 60


def test_derived_paths():
    s = Settings(data_dir=Path("/tmp/casi-test-data"))
    assert s.resolved_workspace_dir == Path("/tmp/casi-test-data/workspace")
    assert s.runs_dir == Path("/tmp/casi-test-data/runs")
    assert s.memory_path == Path("/tmp/casi-test-data/memory.jsonl")
    assert s.audit_path == Path("/tmp/casi-test-data/audit.jsonl")


def test_explicit_workspace_dir_wins():
    s = Settings(data_dir=Path("/tmp/x"), workspace_dir=Path("/tmp/custom-ws"))
    assert s.resolved_workspace_dir == Path("/tmp/custom-ws")


def test_env_override(monkeypatch):
    monkeypatch.setenv("CASI_MAX_WORKERS", "8")
    monkeypatch.setenv("CASI_DATA_DIR", "/tmp/casi-env-data")
    monkeypatch.setenv("CASI_DEFAULT_PROVIDER", "ollama")
    monkeypatch.setenv("CASI_API_KEY", "sekret")
    s = Settings()
    assert s.max_workers == 8
    assert s.data_dir == Path("/tmp/casi-env-data")
    assert s.default_provider == "ollama"
    assert s.api_key == "sekret"
    # unrelated env must not leak in
    assert "CASI_API_KEY" in os.environ


def test_ensure_dirs(tmp_path):
    s = Settings(data_dir=tmp_path / "data")
    s.ensure_dirs()
    assert (tmp_path / "data").is_dir()
    assert (tmp_path / "data" / "workspace").is_dir()
    assert (tmp_path / "data" / "runs").is_dir()
    # idempotent
    s.ensure_dirs()
