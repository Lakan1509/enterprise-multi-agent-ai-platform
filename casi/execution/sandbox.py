"""Sandboxed command execution for CASI agents.

Two backends:

- ``"subprocess"`` (the one actually used): runs the command in a hardened
  local subprocess. ``cwd`` must resolve inside ``work_root`` (escape raises
  :class:`SecurityError`); the environment is scrubbed to a minimal
  allowlist; ``HOME`` is pointed at ``work_root``; ``resource`` limits
  (CPU seconds, address space) are applied where available; a timeout is
  always enforced; stdout/stderr are capped at 256 KiB each.
- ``"docker"``: runs ``docker run --rm -v <cwd>:/work -w /work <image>
  <cmd...>``. Chosen only when ``docker info`` succeeds within 3 seconds.
  Kept intentionally simple and untested when docker is absent — see the
  docstring of :meth:`Sandbox._run_docker`.

Backend detection is lazy, cached, and never raises: any detection failure
falls back to ``"subprocess"``.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

#: Max bytes captured per stream.
_OUTPUT_CAP_BYTES = 256 * 1024
#: Address-space limit for child processes (2 GiB) where `resource` exists.
_AS_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
#: Extra CPU seconds beyond the wall-clock timeout before RLIMIT_CPU kills.
_CPU_GRACE_S = 5


class SecurityError(Exception):
    """Raised when a sandbox operation would escape its jail."""


@dataclass
class ExecResult:
    """Outcome of one sandboxed command run."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float


class Sandbox:
    """Runs commands jailed under ``work_root``.

    Args:
        work_root: Directory the sandbox is allowed to operate in. Every
            ``run`` requires ``cwd`` to resolve inside it.
    """

    #: Environment variables a child process is allowed to inherit/receive.
    ENV_ALLOWLIST = frozenset(
        {"PATH", "PYTHONPATH", "HOME", "LANG", "LC_ALL", "PYTHONUNBUFFERED"}
    )

    def __init__(self, work_root: str | Path) -> None:
        self._work_root = Path(work_root).resolve()
        self._backend: str | None = None  # lazy detection, cached

    # ------------------------------------------------------------------
    # backend detection
    # ------------------------------------------------------------------
    @property
    def backend(self) -> str:
        """``"docker"`` if ``docker info`` succeeds within 3s, else ``"subprocess"``."""
        if self._backend is None:
            self._backend = self._detect_backend()
        return self._backend

    def _detect_backend(self) -> str:
        """Detect the backend. Never raises — failure means subprocess."""
        try:
            proc = subprocess.run(
                ["docker", "info"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            return "docker" if proc.returncode == 0 else "subprocess"
        except Exception:
            return "subprocess"

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def run(
        self,
        cmd: list[str],
        cwd: str | Path,
        timeout_s: int = 60,
        env: dict | None = None,
    ) -> ExecResult:
        """Run ``cmd`` jailed under ``work_root``.

        Raises:
            ValueError: If ``cmd`` is empty.
            SecurityError: If ``cwd`` resolves outside ``work_root``.
        """
        if not cmd:
            raise ValueError("cmd must be a non-empty list of program arguments")
        cmd = [str(part) for part in cmd]
        cwd_resolved = Path(cwd).resolve()
        if cwd_resolved != self._work_root and self._work_root not in cwd_resolved.parents:
            raise SecurityError(
                f"cwd {cwd_resolved} is outside the sandbox work root {self._work_root}"
            )
        if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
            timeout_s = 60

        if self.backend == "docker":
            return self._run_docker(cmd, cwd_resolved, timeout_s, env)
        return self._run_subprocess(cmd, cwd_resolved, timeout_s, env)

    # ------------------------------------------------------------------
    # subprocess backend
    # ------------------------------------------------------------------
    @staticmethod
    def _resource_limits_fn(timeout_s: float):
        """Return a preexec_fn applying CPU/AS limits, or None if unavailable."""

        def _apply() -> None:
            try:
                import resource

                cpu = int(timeout_s) + _CPU_GRACE_S
                resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
                resource.setrlimit(
                    resource.RLIMIT_AS, (_AS_LIMIT_BYTES, _AS_LIMIT_BYTES)
                )
            except Exception:
                pass

        try:
            import resource  # noqa: F401  (availability probe)

            return _apply
        except ImportError:
            return None

    def _scrubbed_env(self, env: dict | None) -> dict[str, str]:
        """Build the child environment: allowlisted vars only."""
        child = {
            key: value
            for key, value in os.environ.items()
            if key in self.ENV_ALLOWLIST
        }
        if env:
            for key, value in env.items():
                if key in self.ENV_ALLOWLIST:
                    child[key] = str(value)
        child["HOME"] = str(self._work_root)
        return child

    def _run_subprocess(
        self,
        cmd: list[str],
        cwd: Path,
        timeout_s: float,
        env: dict | None,
    ) -> ExecResult:
        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd),
                env=self._scrubbed_env(env),
                capture_output=True,
                text=True,
                timeout=timeout_s,
                preexec_fn=self._resource_limits_fn(timeout_s),
            )
            timed_out = False
            returncode = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = -1
            stdout = exc.stdout or "" if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr or "" if isinstance(exc.stderr, str) else ""
        duration_s = time.monotonic() - start
        return ExecResult(
            returncode=returncode,
            stdout=stdout[:_OUTPUT_CAP_BYTES],
            stderr=stderr[:_OUTPUT_CAP_BYTES],
            timed_out=timed_out,
            duration_s=duration_s,
        )

    # ------------------------------------------------------------------
    # docker backend (used only when docker is present; not covered by tests)
    # ------------------------------------------------------------------
    def _run_docker(
        self,
        cmd: list[str],
        cwd: Path,
        timeout_s: float,
        env: dict | None,
    ) -> ExecResult:
        """Run ``cmd`` in a throwaway container with ``cwd`` mounted at /work.

        NOTE: intentionally simple and untested when docker is absent. The
        image can be overridden with the ``CASI_DOCKER_IMAGE`` environment
        variable (default ``python:3.11-slim``).
        """
        image = os.environ.get("CASI_DOCKER_IMAGE", "python:3.11-slim")
        docker_cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{cwd}:/work",
            "-w",
            "/work",
        ]
        if env:
            for key, value in env.items():
                if key in self.ENV_ALLOWLIST:
                    docker_cmd += ["-e", f"{key}={value}"]
        docker_cmd += [image] + cmd
        start = time.monotonic()
        try:
            proc = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            timed_out = False
            returncode = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = -1
            stdout = exc.stdout or "" if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr or "" if isinstance(exc.stderr, str) else ""
        duration_s = time.monotonic() - start
        return ExecResult(
            returncode=returncode,
            stdout=stdout[:_OUTPUT_CAP_BYTES],
            stderr=stderr[:_OUTPUT_CAP_BYTES],
            timed_out=timed_out,
            duration_s=duration_s,
        )
