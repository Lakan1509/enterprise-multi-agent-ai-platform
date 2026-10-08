"""Tests for casi.execution.sandbox (strict namespace isolation).

These tests run the REAL sandbox on this VM and assert actual isolation
properties — they do not merely check API shape:

* the target runs as PID 1 in a fresh PID namespace, as uid/gid 65534
* the host filesystem is invisible (``/etc/passwd`` unreadable, ``..``
  cannot escape, host marker files unreachable, ``/`` shows only jail dirs)
* network egress fails, including to 127.0.0.1 (no loopback in the netns)
* resource abuse is contained: >1 MiB stdout is truncated+killed, fork
  bombs hit RLIMIT_NPROC, huge mallocs hit RLIMIT_AS, wall-clock timeouts
  kill the whole process group with no strays
* if namespaces cannot be established the command is REFUSED
  (:exc:`SandboxUnavailable`) — never run on the host

Namespace tests require root (the uid_map setup needs privilege); they are
skipped for non-root users. Pure API-validation tests run regardless.
"""

import os
import subprocess
import sys
import time

import pytest

from casi.execution.sandbox import ExecResult, Sandbox, SandboxUnavailable, SecurityError

needs_root = pytest.mark.skipif(
    os.geteuid() != 0, reason="namespace sandbox needs root for uid_map setup"
)


def _sandbox(tmp_path) -> Sandbox:
    return Sandbox(tmp_path)


def _py(code: str) -> list[str]:
    """Run a snippet with the jail's python3 (resolved via PATH inside)."""
    return ["python3", "-c", code]


# --- basic execution -------------------------------------------------------


@needs_root
def test_run_echo_returns_stdout(tmp_path):
    sb = _sandbox(tmp_path)
    result = sb.run(_py("print('hi')"), cwd=tmp_path, timeout_s=30)
    assert isinstance(result, ExecResult)
    assert result.returncode == 0
    assert result.stdout.strip() == "hi"
    assert result.timed_out is False
    assert result.truncated is False
    assert result.duration_s >= 0


@needs_root
def test_run_in_subdirectory_of_work_root(tmp_path):
    sub = tmp_path / "goal-1"
    sub.mkdir()
    sb = _sandbox(tmp_path)
    result = sb.run(_py("import os; print(os.getcwd())"), cwd=sub, timeout_s=30)
    assert result.returncode == 0
    assert result.stdout.strip() == "/work/goal-1"


@needs_root
def test_run_failing_command_reports_returncode(tmp_path):
    sb = _sandbox(tmp_path)
    result = sb.run(_py("import sys; sys.exit(3)"), cwd=tmp_path, timeout_s=30)
    assert result.returncode == 3
    assert result.timed_out is False


@needs_root
def test_sys_executable_remaps_into_jail(tmp_path):
    # An absolute host python (e.g. a venv symlink to /usr/bin/python3) is
    # remapped to its in-jail path instead of being refused.
    sb = _sandbox(tmp_path)
    result = sb.run([sys.executable, "-c", "print('remapped')"], cwd=tmp_path, timeout_s=30)
    assert result.returncode == 0
    assert result.stdout.strip() == "remapped"


def test_empty_cmd_raises_value_error(tmp_path):
    sb = _sandbox(tmp_path)
    with pytest.raises(ValueError):
        sb.run([], cwd=tmp_path)


def test_cwd_escape_raises_security_error(tmp_path):
    sb = _sandbox(tmp_path)
    with pytest.raises(SecurityError):
        sb.run(_py("print('nope')"), cwd="/etc", timeout_s=30)


def test_cwd_dotdot_escape_raises_security_error(tmp_path):
    sb = _sandbox(tmp_path)
    escape = tmp_path / "sub" / ".." / ".."
    with pytest.raises(SecurityError):
        sb.run(_py("print('nope')"), cwd=escape, timeout_s=30)


def test_absolute_command_outside_jail_raises_security_error(tmp_path):
    sb = _sandbox(tmp_path)
    with pytest.raises(SecurityError):
        sb.run(["/nonexistent_xyz_123/no-such-binary"], cwd=tmp_path, timeout_s=30)


# --- namespace isolation: identity -----------------------------------------


@needs_root
def test_target_is_pid_1_in_new_pid_namespace(tmp_path):
    sb = _sandbox(tmp_path)
    result = sb.run(_py("import os; print(os.getpid())"), cwd=tmp_path, timeout_s=30)
    assert result.returncode == 0
    assert result.stdout.strip() == "1"


@needs_root
def test_target_runs_as_non_root_nobody(tmp_path):
    sb = _sandbox(tmp_path)
    result = sb.run(
        _py("import os; print(os.getuid(), os.getgid())"), cwd=tmp_path, timeout_s=30
    )
    assert result.returncode == 0
    uid, gid = result.stdout.split()
    assert (uid, gid) == ("65534", "65534")


# --- namespace isolation: filesystem ---------------------------------------


@needs_root
def test_etc_passwd_is_not_readable(tmp_path):
    sb = _sandbox(tmp_path)
    result = sb.run(_py("print(open('/etc/passwd').read())"), cwd=tmp_path, timeout_s=30)
    assert result.returncode != 0  # /etc is an empty dir in the jail


@needs_root
def test_host_files_are_invisible(tmp_path):
    marker = f"/tmp/CASI_HOST_MARKER_{os.getpid()}"
    with open(marker, "w") as fh:
        fh.write("top-secret")
    try:
        sb = _sandbox(tmp_path)
        code = (
            "import os; print("
            f"os.path.exists({marker!r}), "
            "os.path.exists('/home'), "
            "os.path.exists('/proc/1/cmdline'))"
        )
        result = sb.run(_py(code), cwd=tmp_path, timeout_s=30)
        assert result.returncode == 0
        assert result.stdout.strip() == "False False False", result.stdout
    finally:
        os.unlink(marker)


@needs_root
def test_dotdot_traversal_cannot_escape_jail(tmp_path):
    marker = f"/tmp/CASI_TRAVERSAL_{os.getpid()}"
    with open(marker, "w") as fh:
        fh.write("top-secret")
    sub = tmp_path / "sub"
    sub.mkdir()
    try:
        sb = _sandbox(tmp_path)
        # From /work/sub, ../../../tmp/... would reach the host's /tmp if
        # the jail were just a chdir; inside the jail it must fail.
        code = (
            "import os; os.chdir('/work/sub'); "
            f"print(open('../../../tmp/{os.path.basename(marker)}').read())"
        )
        result = sb.run(_py(code), cwd=sub, timeout_s=30)
        assert result.returncode != 0, result.stdout
        assert "top-secret" not in result.stdout
    finally:
        os.unlink(marker)


@needs_root
def test_jail_root_shows_only_jail_directories(tmp_path):
    sb = _sandbox(tmp_path)
    result = sb.run(_py("import os; print(sorted(os.listdir('/')))"), cwd=tmp_path, timeout_s=30)
    assert result.returncode == 0
    entries = set(result.stdout.strip().strip("[]").replace("'", "").split(", "))
    assert "home" not in entries and "root" not in entries and "var" not in entries
    assert {"work", "usr", "etc", "tmp", "dev"}.issubset(entries)


@needs_root
def test_work_dir_is_writable_and_persists_on_host(tmp_path):
    sb = _sandbox(tmp_path)
    result = sb.run(_py("open('/work/from_jail.txt','w').write('jail-data')"), cwd=tmp_path, timeout_s=30)
    assert result.returncode == 0
    assert (tmp_path / "from_jail.txt").read_text() == "jail-data"


# --- namespace isolation: network ------------------------------------------


@needs_root
def test_network_egress_fails(tmp_path):
    sb = _sandbox(tmp_path)
    code = (
        "import socket; "
        "socket.create_connection(('8.8.8.8', 53), timeout=5); "
        "print('CONNECTED-BAD')"
    )
    result = sb.run(_py(code), cwd=tmp_path, timeout_s=30)
    assert "CONNECTED-BAD" not in result.stdout
    assert result.returncode != 0  # ENETUNREACH, no route in the netns


@needs_root
def test_loopback_is_down(tmp_path):
    sb = _sandbox(tmp_path)
    code = (
        "import socket; "
        "socket.create_connection(('127.0.0.1', 9), timeout=5); "
        "print('CONNECTED-BAD')"
    )
    result = sb.run(_py(code), cwd=tmp_path, timeout_s=30)
    assert "CONNECTED-BAD" not in result.stdout
    assert result.returncode != 0  # lo is not up in the netns


# --- resource containment --------------------------------------------------


@needs_root
def test_stdout_over_1mib_is_truncated_and_killed(tmp_path):
    sb = _sandbox(tmp_path)
    code = "import sys; sys.stdout.write('x' * (3 * 1024 * 1024)); sys.stdout.flush()"
    start = time.monotonic()
    result = sb.run(_py(code), cwd=tmp_path, timeout_s=30)
    duration = time.monotonic() - start
    assert result.truncated is True
    assert len(result.stdout.encode("utf-8")) <= 1024 * 1024
    assert result.returncode == -9  # SIGKILLed for exceeding the cap
    assert duration < 15  # killed promptly, not left running


@needs_root
def test_fork_bomb_is_contained_by_nproc_limit(tmp_path):
    sb = _sandbox(tmp_path)
    code = (
        "import os\n"
        "n = 0\n"
        "try:\n"
        "    for _ in range(500):\n"
        "        pid = os.fork()\n"
        "        if pid == 0:\n"
        "            os._exit(0)\n"
        "        n += 1\n"
        "except OSError:\n"
        "    pass\n"
        "print('forks', n)\n"
    )
    start = time.monotonic()
    result = sb.run(_py(code), cwd=tmp_path, timeout_s=30)
    duration = time.monotonic() - start
    assert result.returncode == 0
    assert duration < 15  # contained quickly, host unaffected
    forks = int(result.stdout.strip().split()[1])
    assert forks < 100  # RLIMIT_NPROC stops the bomb early


@needs_root
def test_huge_malloc_is_contained_by_as_limit(tmp_path):
    sb = _sandbox(tmp_path)
    code = "x = bytearray(2**31); print('allocated-BAD')"
    start = time.monotonic()
    result = sb.run(_py(code), cwd=tmp_path, timeout_s=30)
    duration = time.monotonic() - start
    assert "allocated-BAD" not in result.stdout
    assert result.returncode != 0  # MemoryError under the 1 GiB RLIMIT_AS
    assert duration < 15


@needs_root
def test_wall_clock_timeout_kills_process_group_without_strays(tmp_path):
    sb = _sandbox(tmp_path)
    marker = f"CASI_SANDBOX_TIMEOUT_{os.getpid()}"
    start = time.monotonic()
    result = sb.run(
        ["bash", "-c", f"exec -a {marker} sleep 1000"], cwd=tmp_path, timeout_s=2
    )
    duration = time.monotonic() - start
    assert result.timed_out is True
    assert result.returncode == -1
    assert duration < 10
    time.sleep(1.0)  # let the SIGKILL take effect
    # No stray sleep may survive: the whole process group is killed.
    ps = subprocess.run(
        ["pgrep", "-f", f"[C]ASI_SANDBOX_TIMEOUT_{os.getpid()}"],
        capture_output=True,
        text=True,
    )
    assert ps.stdout.strip() == "", f"stray process survived: {ps.stdout}"


# --- environment scrubbing -------------------------------------------------


@needs_root
def test_env_scrubbing_hides_non_allowlisted_vars(tmp_path):
    sb = _sandbox(tmp_path)
    os.environ["SECRET_X"] = "s3cr3t"
    try:
        code = "import os; print(os.environ.get('SECRET_X', 'ABSENT'))"
        result = sb.run(
            _py(code),
            cwd=tmp_path,
            timeout_s=30,
            env={"SECRET_X": "1", "PATH": os.environ.get("PATH", "")},
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "ABSENT"
    finally:
        del os.environ["SECRET_X"]


@needs_root
def test_env_allowlists_path_and_sets_home_to_jail_work(tmp_path):
    sb = _sandbox(tmp_path)
    code = (
        "import os; print('PATH_OK' if os.environ.get('PATH') else 'PATH_MISSING');"
        "print(os.environ.get('HOME'))"
    )
    result = sb.run(_py(code), cwd=tmp_path, timeout_s=30)
    assert result.returncode == 0
    lines = result.stdout.split()
    assert lines[0] == "PATH_OK"
    assert lines[1] == "/work"  # in-jail path of the work root


# --- strict refusal: never run on the host ---------------------------------


def test_sandbox_refuses_when_namespaces_unavailable(tmp_path, monkeypatch):
    """If unshare fails, run() must raise and the command must never run."""
    sb = _sandbox(tmp_path)

    def _no_unshare(*args, **kwargs):
        raise OSError(1, "user namespaces disabled")

    monkeypatch.setattr(os, "unshare", _no_unshare)
    sentinel = tmp_path / "pwned_by_host_fallback"
    with pytest.raises(SandboxUnavailable):
        sb.run(
            ["python3", "-c", f"open({str(sentinel)!r},'w').write('x')"],
            cwd=tmp_path,
            timeout_s=10,
        )
    assert not sentinel.exists()  # the payload never executed anywhere


def test_sandbox_unavailable_is_a_security_error(tmp_path):
    assert issubclass(SandboxUnavailable, SecurityError)


def test_unsupported_mode_raises_value_error(tmp_path):
    with pytest.raises(ValueError):
        Sandbox(tmp_path, mode="host")
    with pytest.raises(ValueError):
        Sandbox(tmp_path, mode="docker")


def test_mode_env_var_is_honored(tmp_path, monkeypatch):
    monkeypatch.setenv("CASI_SANDBOX_MODE", "strict")
    Sandbox(tmp_path)  # ok
    monkeypatch.setenv("CASI_SANDBOX_MODE", "subprocess")
    with pytest.raises(ValueError):
        Sandbox(tmp_path)


# --- hygiene -----------------------------------------------------------------


@needs_root
def test_no_mount_leaks_after_runs(tmp_path):
    def _jail_mounts():
        return {
            line
            for line in open("/proc/self/mountinfo").read().splitlines()
            if "casi-jail" in line
        }

    before = _jail_mounts()
    sb = _sandbox(tmp_path)
    for _ in range(3):
        result = sb.run(_py("print('x')"), cwd=tmp_path, timeout_s=30)
        assert result.returncode == 0
    assert _jail_mounts() <= before


# --- venv exposure / jail usability ------------------------------------------


@needs_root
def test_venv_exposure_probe_skips_hostile_filesystem(tmp_path, monkeypatch):
    """A venv unreadable by the sandbox uid is skipped, not half-mounted."""
    from casi.execution import sandbox as sbmod

    monkeypatch.setattr(sbmod, "_readable_by_sandbox_uid", lambda p: False)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(sys, "base_prefix", "/usr")
    (tmp_path / "venv" / "pyvenv.cfg").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "venv" / "pyvenv.cfg").write_text("x=1")
    with pytest.warns(RuntimeWarning, match="not readable by the sandbox uid"):
        assert sbmod._detect_venv() is None


@needs_root
def test_jail_dev_nodes_usable(tmp_path):
    """/dev/null must be openable by the unprivileged target (pytest needs it)."""
    sb = _sandbox(tmp_path)
    result = sb.run(
        _py("open('/dev/null', 'r').close(); open('/dev/null', 'w').write('x'); print('dev-ok')"),
        cwd=tmp_path,
        timeout_s=30,
    )
    assert result.returncode == 0, result.stderr[-300:]
    assert "dev-ok" in result.stdout


@needs_root
def test_jail_runs_pytest_when_available(tmp_path):
    """The jail python can run pytest (system or venv-provided)."""
    sb = _sandbox(tmp_path)
    probe = sb.run(_py("import pytest"), cwd=tmp_path, timeout_s=30)
    if probe.returncode != 0:
        pytest.skip("pytest not importable by the jail python in this env")
    (tmp_path / "test_tiny.py").write_text("def test_tiny():\n    assert True\n")
    # The sandbox target runs as an unprivileged uid: test files must be
    # world-readable (the real flow goes through workspace.write, which
    # enforces 0644; here we emulate it explicitly).
    os.chmod(tmp_path / "test_tiny.py", 0o644)
    result = sb.run(
        ["python3", "-m", "pytest", "test_tiny.py", "-q", "-p", "no:cacheprovider"],
        cwd=tmp_path,
        timeout_s=90,
    )
    assert result.returncode == 0, result.stderr[-500:]
    assert "1 passed" in result.stdout
