"""Tests for casi.execution.sandbox against the REAL subprocess backend.

The backend is forced to ``"subprocess"`` (``sb._backend = "subprocess"``) so
these tests exercise the hardened local path deterministically regardless of
whether docker happens to be installed on the machine.
"""

import os
import sys

import pytest

from casi.execution.sandbox import ExecResult, Sandbox, SecurityError


def _sandbox(tmp_path) -> Sandbox:
    sb = Sandbox(tmp_path)
    sb._backend = "subprocess"  # force the real subprocess path under test
    return sb


def _py(*args: str) -> list[str]:
    return [sys.executable, "-c", *args]


# --- basic execution -------------------------------------------------------


def test_run_echo_returns_stdout(tmp_path):
    sb = _sandbox(tmp_path)
    result = sb.run(_py("print('hi')"), cwd=tmp_path, timeout_s=30)

    assert isinstance(result, ExecResult)
    assert result.returncode == 0
    assert result.stdout.strip() == "hi"
    assert result.timed_out is False
    assert result.duration_s >= 0


def test_run_in_subdirectory_of_work_root(tmp_path):
    sub = tmp_path / "goal-1"
    sub.mkdir()
    sb = _sandbox(tmp_path)
    result = sb.run(_py("print('ok')"), cwd=sub, timeout_s=30)
    assert result.returncode == 0
    assert result.stdout.strip() == "ok"


def test_run_failing_command_reports_returncode(tmp_path):
    sb = _sandbox(tmp_path)
    result = sb.run(_py("import sys; sys.exit(3)"), cwd=tmp_path, timeout_s=30)
    assert result.returncode == 3
    assert result.timed_out is False


def test_empty_cmd_raises_value_error(tmp_path):
    sb = _sandbox(tmp_path)
    with pytest.raises(ValueError):
        sb.run([], cwd=tmp_path)


# --- jail ------------------------------------------------------------------


def test_cwd_escape_raises_security_error(tmp_path):
    sb = _sandbox(tmp_path)
    with pytest.raises(SecurityError):
        sb.run(_py("print('nope')"), cwd="/etc", timeout_s=30)


def test_cwd_dotdot_escape_raises_security_error(tmp_path):
    sb = _sandbox(tmp_path)
    escape = tmp_path / "sub" / ".." / ".."
    with pytest.raises(SecurityError):
        sb.run(_py("print('nope')"), cwd=escape, timeout_s=30)


# --- timeout ---------------------------------------------------------------


def test_timeout_kills_and_marks_timed_out(tmp_path):
    sb = _sandbox(tmp_path)
    result = sb.run(["sleep", "5"], cwd=tmp_path, timeout_s=1)
    assert result.timed_out is True
    assert result.returncode == -1


# --- environment scrubbing -------------------------------------------------


def test_env_scrubbing_hides_non_allowlisted_vars(tmp_path):
    sb = _sandbox(tmp_path)
    code = "import os; print(os.environ.get('SECRET_X', 'ABSENT'))"
    result = sb.run(
        _py(code),
        cwd=tmp_path,
        timeout_s=30,
        env={"SECRET_X": "1", "PATH": os.environ.get("PATH", "")},
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "ABSENT"


def test_env_allowlists_path_and_sets_home(tmp_path):
    sb = _sandbox(tmp_path)
    code = (
        "import os; print('PATH_OK' if os.environ.get('PATH') else 'PATH_MISSING');"
        "print(os.environ.get('HOME'))"
    )
    result = sb.run(_py(code), cwd=tmp_path, timeout_s=30)
    assert result.returncode == 0
    lines = result.stdout.split()
    assert lines[0] == "PATH_OK"
    assert lines[1] == str(tmp_path.resolve())


# --- output cap ------------------------------------------------------------


def test_stdout_capped_at_256kib(tmp_path):
    sb = _sandbox(tmp_path)
    code = "import sys; sys.stdout.write('x' * (1024 * 1024)); sys.stdout.write('y')"
    result = sb.run(_py(code), cwd=tmp_path, timeout_s=30)
    assert result.returncode == 0
    assert len(result.stdout.encode()) <= 256 * 1024
    assert result.stdout.endswith("x")  # tail was cut, not the head


# --- backend detection -----------------------------------------------------


def test_backend_detection_never_raises(tmp_path):
    sb = Sandbox(tmp_path)
    assert sb.backend in {"docker", "subprocess"}
    # cached on second access
    assert sb.backend == sb._backend
