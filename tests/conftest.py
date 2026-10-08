"""Shared pytest bootstrap for the CASI test suite.

Inserts the project root (``~/workspace/casi-ai-os/``) at the front of
``sys.path`` so ``import casi`` works no matter which directory pytest is
invoked from.
"""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


class ExecSandbox:
    """Sandbox-shaped test double that REALLY executes commands via subprocess.

    It mirrors the ``casi.execution.sandbox.Sandbox.run`` call shape
    (``run(cmd, cwd, timeout_s, env)`` -> result with ``returncode``,
    ``stdout``, ``stderr``, ``timed_out``) but executes the command directly
    on the host instead of inside the namespace jail. Stream D uses it to
    verify agents against genuine program execution without depending on
    ``casi.execution.sandbox`` — another stream's component, currently being
    rewritten for Milestone 2 (namespace-based isolation).
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, cmd, cwd=None, timeout_s=60, env=None):
        """Execute ``cmd`` for real; never raises on command failure."""
        self.calls.append({"cmd": list(cmd), "cwd": cwd, "timeout_s": timeout_s})
        timeout = timeout_s if isinstance(timeout_s, (int, float)) and timeout_s > 0 else 60
        try:
            proc = subprocess.run(
                [str(part) for part in cmd],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or ""
            err = exc.stderr or ""
            return SimpleNamespace(
                returncode=-1,
                stdout=out if isinstance(out, str) else out.decode("utf-8", "replace"),
                stderr=err if isinstance(err, str) else err.decode("utf-8", "replace"),
                timed_out=True,
            )
        return SimpleNamespace(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            timed_out=False,
        )
