"""Adversarial tests: escape attempts, concurrency, and fail-closed integration.

These tests attack the Milestone 2 security properties directly. They
require root (Linux namespaces) and are skipped otherwise.
"""

from __future__ import annotations

import os
import threading

import pytest

from casi.execution.sandbox import Sandbox, SandboxUnavailable, SecurityError

needs_root = pytest.mark.skipif(
    os.geteuid() != 0, reason="namespace sandbox tests require root"
)


def _sandbox(work) -> Sandbox:
    return Sandbox(work)


def _py(code: str) -> list[str]:
    return ["python3", "-c", code]


@needs_root
def test_symlink_escape_contained(tmp_path):
    """Symlinks inside the jail cannot reach host files (chroot contains them)."""
    os.symlink("/etc/passwd", tmp_path / "evil_abs")
    os.symlink("../../../../etc/passwd", tmp_path / "evil_rel")
    sb = _sandbox(tmp_path)
    for link in ("evil_abs", "evil_rel"):
        result = sb.run(
            _py(
                "import os\n"
                f"p = '/work/{link}'\n"
                "print('exists', os.path.exists(p))\n"
                "try:\n"
                "    data = open(p).read()\n"
                "    print('READ', len(data))\n"
                "except Exception as e:\n"
                "    print('BLOCKED', type(e).__name__)\n"
            ),
            cwd=tmp_path,
            timeout_s=30,
        )
        assert result.returncode == 0, result.stderr[-300:]
        # Either blocked, or reads the jail's EMPTY /etc (never host data).
        assert "BLOCKED" in result.stdout or "READ 0" in result.stdout or (
            "exists False" in result.stdout
        ), result.stdout
        assert "root:" not in result.stdout  # host /etc/passwd content


@needs_root
def test_chroot_reescape_fails(tmp_path):
    """The unprivileged target cannot chroot/mount its way out."""
    sb = _sandbox(tmp_path)
    result = sb.run(
        _py(
            "import os\n"
            "for op, fn in [('chroot', lambda: os.chroot('/')),"
            " ('mkdir', lambda: os.mkdir('/newroot'))]:\n"
            "    try:\n"
            "        fn(); print(op, 'SUCCEEDED - BAD')\n"
            "    except PermissionError:\n"
            "        print(op, 'denied-ok')\n"
            "    except OSError as e:\n"
            "        print(op, 'denied-ok', e.errno)\n"
        ),
        cwd=tmp_path,
        timeout_s=30,
    )
    assert result.returncode == 0, result.stderr[-300:]
    assert "SUCCEEDED - BAD" not in result.stdout
    assert result.stdout.count("denied-ok") == 2


@needs_root
def test_cannot_write_to_ro_system_trees(tmp_path):
    """The jail's /usr and /venv are read-only for the target."""
    sb = _sandbox(tmp_path)
    result = sb.run(
        _py(
            "for p in ('/usr/pwned', '/venv/pwned', '/etc/pwned'):\n"
            "    try:\n"
            "        open(p, 'w').write('x')\n"
            "        print(p, 'WRITABLE - BAD')\n"
            "    except (PermissionError, OSError):\n"
            "        print(p, 'ro-ok')\n"
        ),
        cwd=tmp_path,
        timeout_s=30,
    )
    assert result.returncode == 0, result.stderr[-300:]
    assert "WRITABLE - BAD" not in result.stdout
    assert result.stdout.count("ro-ok") == 3


@needs_root
def test_concurrent_sandbox_runs_isolated(tmp_path):
    """Concurrent runs don't interfere; each sees only its own jail."""
    import tempfile

    dirs = [tempfile.mkdtemp(prefix=f"conc-{i}-") for i in range(4)]
    try:
        sbs = [_sandbox(d) for d in dirs]
        results = [None] * 4
        errors = []

        def worker(i):
            try:
                results[i] = sbs[i].run(
                    _py(
                        "import os, time; "
                        "open('/work/marker', 'w').write('hello'); "
                        "time.sleep(0.5); "
                        "print(open('/work/marker').read(), os.getpid())"
                    ),
                    cwd=dirs[i],
                    timeout_s=60,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        assert not errors, errors
        for i, r in enumerate(results):
            assert r is not None and r.returncode == 0, (i, r.stderr[-200:] if r else None)
            assert "hello" in r.stdout
        # Markers did not leak across work roots.
        for i, d in enumerate(dirs):
            assert os.listdir(d) == ["marker"], os.listdir(d)
    finally:
        import shutil

        for d in dirs:
            shutil.rmtree(d, ignore_errors=True)


def test_kernel_viewer_role_cannot_execute_code(tmp_path):
    """End-to-end: a VIEWER run's EXECUTE_CODE attempt fails closed in the kernel."""
    from casi.config import Settings
    from casi.kernel import AIKernel
    from casi.planner import TaskSpec
    from casi.scheduler import DAGScheduler
    from casi.security.permissions import Role

    from tests.test_kernel import FakeAgent, FakeApprovalGate, FakeAudit, FakeRegistry

    settings = Settings(data_dir=tmp_path / "data")
    kernel = AIKernel(settings)
    kernel.registry = FakeRegistry(FakeAgent())
    kernel.scheduler = DAGScheduler(kernel.registry, FakeAudit(), max_workers=2)
    kernel.approvals = FakeApprovalGate()
    kernel.audit = FakeAudit()
    goal = kernel.create_goal("probe goal")
    factory = kernel._make_ctx_factory(goal, Role.VIEWER)
    task = TaskSpec(id="t1", name="probe", description="probe", agent_capability="coder")

    from casi.agents.base import require_capability
    from casi.security.permissions import Capability, PermissionDenied

    ctx = factory(task)
    assert ctx.role == Role.VIEWER
    with pytest.raises(PermissionDenied):
        require_capability(ctx, Capability.EXECUTE_CODE)
    # ...but reads are allowed for VIEWER.
    require_capability(ctx, Capability.READ_WORKSPACE)


@needs_root
def test_sandbox_unavailable_raises_not_runs(tmp_path, monkeypatch):
    """If isolation cannot be established, execution is refused (no host fallback)."""
    import casi.execution.sandbox as sbmod

    def _boom(*a, **k):
        raise OSError(1, "nope")

    monkeypatch.setattr(sbmod, "_mount", _boom)
    sb = _sandbox(tmp_path)
    with pytest.raises(SandboxUnavailable):
        sb.run(_py("print('should never print')"), cwd=tmp_path, timeout_s=10)
