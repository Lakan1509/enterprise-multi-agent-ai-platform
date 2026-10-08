"""Namespace-isolated sandboxed command execution for CASI agents.

The default (and only) backend, ``"strict"``, runs every command inside a
fresh set of Linux namespaces created with :func:`os.unshare`:

* **User namespace** – the helper starts as uid 0 (mapped to the outer uid,
  plus a 65534→65534 mapping written by the parent) and then irreversibly
  drops to uid/gid 65534 (``nobody``) before executing the target. The
  target therefore reports ``os.getuid() != 0`` and cannot regain privilege.
* **Mount namespace** – the parent mounts a tmpfs as the new root (256 MiB)
  plus a private tmpfs at ``/tmp`` (64 MiB); inside, the host's ``/usr`` is
  bind-mounted read-only (so a normal userspace, including ``python3``,
  exists); ``/etc`` is an empty, root-owned, unwritable directory;
  ``/dev`` carries only null/zero/urandom; and **only** ``work_root`` is
  bind-mounted read-write at ``/work``. When this process runs inside a
  virtualenv (or ``CASI_SANDBOX_VENV`` names one), it is bind-mounted
  **read-only** at ``/venv`` and ``/venv/bin`` is prepended to the jail
  ``PATH``, so jailed commands use the same interpreter and packages
  (e.g. ``pytest``). ``chroot`` then confines the
  process to the new root, so no host path is reachable – not even via
  ``..`` traversal. (``pivot_root`` would be stronger but the kernel
  rejects it with ``EINVAL`` inside the nested user namespace when the new
  root's superblock belongs to the parent user namespace; ``chroot`` is
  safe here because the process holds no directory fds to the outside and
  irreversibly drops to the unprivileged sandbox uid, so it cannot
  mount/mknod/re-chroot its way out.) No ``/proc`` is mounted inside (a
  fresh proc instance cannot be mounted in the nested user namespace on
  this kernel, and bind-mounting the host's ``/proc`` would leak host
  PIDs).
* **PID namespace** – the target becomes PID 1 and sees no host processes.
  When it exits, the kernel tears the namespace down, killing strays.
* **Network namespace** – created without bringing any interface (including
  loopback) up, so all egress – even to 127.0.0.1 – fails.

Resource containment: ``RLIMIT_CPU`` (timeout + grace), ``RLIMIT_AS``
(1 GiB), ``RLIMIT_NPROC`` (32), ``RLIMIT_FSIZE`` (64 MiB). Stdout/stderr are
captured through pipes with a hard 1 MiB cap per stream – exceeding it kills
the whole process group with SIGKILL and the result is flagged
``truncated=True``. A wall-clock timeout kills the process group and sets
``timed_out=True``.

Never silently falls back: if namespaces, the ``unshare`` call, any mount,
``chroot``, the uid/gid map setup, or any other isolation step fails, a
:exc:`SandboxUnavailable` is raised and the command is **not** executed on
the host. There is no host-execution path in this module.

``CASI_SANDBOX_MODE``: only ``"strict"`` is supported (the default). Any
other value raises ``ValueError`` at construction time. There is
deliberately no ``"host"``/``"subprocess"`` fallback mode, and the
pre-Milestone-2 docker backend was removed: it could not be exercised on
this VM and a container runtime is outside this project's dependency set.

What this sandbox does NOT protect against
------------------------------------------
* **Kernel exploits.** A namespace jail shares the host kernel; a kernel
  privilege-escalation bug defeats it. This is containment for untrusted
  *code*, not a security boundary against a nation-state attacker.
* **Side channels** (timing, CPU cache, memory-pressure stalls, etc.).
* **Host resource exhaustion beyond the per-run rlimits.** The limits only
  bound a single run; a deliberately wasteful-but-legal run still consumes
  up to 1 GiB RAM / 32 processes / its CPU slice while it lives.
* **A compromised host userspace.** The jail bind-mounts the *host's*
  ``/usr`` read-only and trusts it. If the host's ``/usr/bin/python3`` is
  malicious, the jail is too.
* **Exfiltration via the work dir.** ``/work`` is intentionally read-write
  and persists on the host; treat files written by untrusted runs as
  untrusted input.
* **Syscall filtering.** No seccomp-bpf filter is applied (future work);
  containment rests on namespaces + uid drop + rlimits.

Side effects on the host: ``work_root`` itself (not its contents, except
for directories on the ``cwd`` path, see below) is ``chown``-ed to uid/gid
65534 so the unprivileged target can write to ``/work``. Directories on the
path from ``work_root`` down to the run's ``cwd`` are likewise chown-ed to
the sandbox uid so the target can ``chdir`` there; file contents and
ownership below those directories are untouched. A per-run staging directory is created under the
system temp dir (with two tmpfs mounts that are unmounted afterwards) and
removed afterwards; all jail mounts are private to the new mount namespace
and never propagate to the host.
"""

from __future__ import annotations

import ctypes
import errno
import os
import resource
import selectors
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import c_char_p, c_int, c_ulong
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

#: Max bytes captured per stream. Exceeding it SIGKILLs the child.
_OUTPUT_CAP_BYTES = 1024 * 1024
#: Address-space limit for the jailed process tree (1 GiB).
_AS_LIMIT_BYTES = 1024 * 1024 * 1024
#: Max processes/threads for the jailed uid.
_NPROC_LIMIT = 32
#: Max size of a single file the jailed process may create (64 MiB).
_FSIZE_LIMIT_BYTES = 64 * 1024 * 1024
#: Extra CPU seconds beyond the wall-clock timeout before RLIMIT_CPU kills.
_CPU_GRACE_S = 5
#: Unprivileged uid/gid the target runs as inside the jail.
_SANDBOX_UID = 65534
_SANDBOX_GID = 65534
#: Size of the tmpfs used as the jail's new root.
_NEWROOT_TMPFS_SIZE = "256m"
#: Size of the tmpfs mounted at /tmp inside the jail.
_JAIL_TMP_SIZE = "64m"
#: Exit code used by helper processes when isolation setup fails.
_SETUP_FAILED_CODE = 99
#: How long the parent waits for each setup-handshake step before giving up.
_HANDSHAKE_TIMEOUT_S = 15.0
#: Default PATH inside the jail (matches the bind-mounted /usr layout).
_JAIL_DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"
#: Host prefixes whose realpath is bind-mounted read-only at the same path.
_SYSTEM_JAIL_PREFIXES = ("/usr",)
#: Jail path where the host Python venv is exposed read-only (if detected).
_VENV_JAIL_PATH = "/venv"
#: Env var overriding venv exposure: a path to expose, or "" to disable.
_VENV_ENV_VAR = "CASI_SANDBOX_VENV"


def _detect_venv() -> Path | None:
    """Return the host venv to expose read-only inside the jail, if any.

    ``CASI_SANDBOX_VENV`` overrides auto-detection: a directory path is
    exposed, an empty value disables exposure entirely. Otherwise, when
    this process runs inside a virtualenv (``sys.prefix != sys.base_prefix``
    with a ``pyvenv.cfg``), that venv is exposed so jailed commands can use
    the same interpreter and installed packages (e.g. ``pytest``).
    The venv is bind-mounted **read-only**; it is trusted host software,
    not untrusted input.

    The candidate is pre-flight checked for readability by the sandbox uid
    (65534): some filesystems (btrfs on this VM, verified 2026-10-08) deny
    unprivileged user-namespace processes access regardless of permission
    bits. An unusable candidate is skipped with a warning — the sandbox
    itself still runs fully namespaced; only the venv package source is
    dropped (the jail's system python remains available).
    """
    override = os.environ.get(_VENV_ENV_VAR)
    if override is not None:
        if not override.strip():
            return None
        candidate = Path(override).resolve()
        if not candidate.is_dir():
            return None
        return candidate if _readable_by_sandbox_uid(candidate) else None
    prefix = Path(sys.prefix).resolve()
    base = Path(getattr(sys, "base_prefix", sys.prefix)).resolve()
    if prefix != base and (prefix / "pyvenv.cfg").is_file():
        if _readable_by_sandbox_uid(prefix):
            return prefix
        import warnings

        warnings.warn(
            f"CASI_SANDBOX_VENV: venv at {prefix} is not readable by the "
            "sandbox uid (filesystem/userns quirk); venv will not be "
            "exposed inside the jail. Set CASI_SANDBOX_VENV to a venv on a "
            "userns-compatible filesystem (ext4/overlayfs/tmpfs) or install "
            "needed packages into the system python.",
            RuntimeWarning,
            stacklevel=2,
        )
    return None


def _readable_by_sandbox_uid(path: Path) -> bool:
    """True if the sandbox uid/gid can open ``path`` for reading.

    Forks a short-lived child that drops to the sandbox uid/gid and tries
    the open; the child touches nothing else. Used to detect filesystems
    (e.g. btrfs) that deny unprivileged user-namespace access.
    """
    probe = os.path.join(str(path), "pyvenv.cfg")
    if not os.path.isfile(probe):
        probe = str(path)
    pid = os.fork()
    if pid == 0:  # child: never returns
        try:
            os.setgroups([])
            os.setresgid(_SANDBOX_GID, _SANDBOX_GID, _SANDBOX_GID)
            os.setresuid(_SANDBOX_UID, _SANDBOX_UID, _SANDBOX_UID)
            fd = os.open(probe, os.O_RDONLY)
            os.close(fd)
        except Exception:
            os._exit(1)
        os._exit(0)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status) == 0

# Status-pipe protocol (child -> parent), one line per message.
_STATUS_READY = b"ready\n"
_STATUS_OK = b"ok\n"
_STATUS_ERR_PREFIX = b"err:"

# mount(2) flags
_MS_BIND = 4096
_MS_REMOUNT = 32
_MS_RDONLY = 1
_MS_REC = 16384
_MS_PRIVATE = 1 << 18
_MNT_DETACH = 2

class SecurityError(Exception):
    """Raised when a sandbox operation would escape its jail."""


class SandboxUnavailable(SecurityError):
    """Raised when namespace isolation cannot be established.

    A subclass of :exc:`SecurityError` so existing ``except SecurityError``
    handlers keep working. The command is never executed when this is
    raised – there is no host fallback.
    """


@dataclass
class ExecResult:
    """Outcome of one sandboxed command run."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float
    truncated: bool = False


# ---------------------------------------------------------------------------
# libc bindings
# ---------------------------------------------------------------------------

_libc = ctypes.CDLL(None, use_errno=True)
_libc.mount.argtypes = [c_char_p, c_char_p, c_char_p, c_ulong, c_char_p]
_libc.mount.restype = c_int
_libc.umount2.argtypes = [c_char_p, c_int]
_libc.umount2.restype = c_int


def _mount(src: str | None, dst: str, fstype: str | None, flags: int, opts: str | None) -> None:
    """Thin wrapper around mount(2) that raises OSError on failure."""
    ret = _libc.mount(
        src.encode() if src is not None else None,
        dst.encode(),
        fstype.encode() if fstype is not None else None,
        c_ulong(flags),
        opts.encode() if opts is not None else None,
    )
    if ret != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"mount({src!r} -> {dst!r}, {fstype!r}, {flags:#x}): {os.strerror(err)}")


def _umount(dst: str) -> None:
    """Detach-unmount, ignoring errors (best effort)."""
    try:
        _libc.umount2(dst.encode(), _MNT_DETACH)
    except OSError:
        pass


def _bind_ro(src: str, dst: str) -> None:
    """Bind-mount ``src`` at ``dst`` read-only.

    Raises if the mount cannot be verified read-only afterwards.
    """
    _mount(src, dst, None, _MS_BIND, None)
    try:
        _mount(None, dst, None, _MS_BIND | _MS_REMOUNT | _MS_RDONLY, None)
    except OSError as exc:
        if exc.errno != errno.EPERM:
            raise
        # Some filesystems (btrfs on this VM, verified 2026-10-08) reject
        # the raw remount(2) syscall with EPERM while the mount(8) helper
        # succeeds. Fall back to the helper — still verified below.
        proc = subprocess.run(
            ["mount", "-o", "remount,ro,bind", dst],
            capture_output=True,
            timeout=30,
        )
        if proc.returncode != 0:
            raise OSError(
                exc.errno,
                f"read-only remount of {dst!r} failed: "
                f"{proc.stderr.decode(errors='replace')[:200]}",
            ) from exc
    _assert_ro(dst)


def _assert_ro(dst: str) -> None:
    """Raise unless ``dst`` is currently mounted read-only."""
    want = os.path.realpath(dst)
    with open("/proc/self/mounts", encoding="utf-8") as handle:
        for line in handle:
            parts = line.split()
            if len(parts) >= 4 and os.path.realpath(parts[1]) == want:
                if "ro" in parts[3].split(","):
                    return
                raise OSError(errno.EPERM, f"mount at {dst!r} is not read-only")
    raise OSError(errno.ENOENT, f"no mount found at {dst!r}")


def _write_file(path: str, data: str) -> None:
    with open(path, "w") as fh:
        fh.write(data)


def _fail(status_w: int, message: str) -> NoReturn:
    """Report a setup failure to the parent, then exit without running."""
    try:
        line = _STATUS_ERR_PREFIX + message.replace("\n", " ").encode("utf-8", "replace")[:4000]
        os.write(status_w, line + b"\n")
    except OSError:
        pass
    os._exit(_SETUP_FAILED_CODE)


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------


class Sandbox:
    """Runs commands jailed under ``work_root`` in Linux namespaces.

    Args:
        work_root: Directory the sandbox is allowed to operate in. Every
            ``run`` requires ``cwd`` to resolve inside it. The directory
            itself is chown-ed to the sandbox uid (65534) on the host so
            the unprivileged target can write to ``/work``.
        mode: Sandbox mode. Only ``"strict"`` (namespace isolation) is
            supported; ``None`` reads ``CASI_SANDBOX_MODE`` (default
            ``"strict"``). Anything else raises ``ValueError``.

    .. warning::
        ``run()`` must be called from the main thread: it uses
        :func:`os.fork`, which is unsafe in multi-threaded processes.
    """

    #: Environment variables a child process is allowed to inherit/receive.
    ENV_ALLOWLIST = frozenset(
        {"PATH", "PYTHONPATH", "HOME", "LANG", "LC_ALL", "PYTHONUNBUFFERED"}
    )

    def __init__(self, work_root: str | Path, *, mode: str | None = None) -> None:
        if mode is None:
            mode = os.environ.get("CASI_SANDBOX_MODE", "strict")
        mode = mode.strip().lower()
        if mode != "strict":
            raise ValueError(
                f"unsupported sandbox mode {mode!r}: only 'strict' (Linux "
                "namespace isolation) is supported; there is no host fallback"
            )
        self._mode = mode
        self._work_root = Path(work_root).resolve()
        self._venv = _detect_venv()
        self._venv_site_jail: str | None = None
        if self._venv is not None:
            # The venv's python3 is typically a symlink to /usr/bin/python3,
            # which defeats venv auto-detection inside the jail. Expose the
            # site-packages explicitly via PYTHONPATH instead.
            matches = sorted((self._venv / "lib").glob("python*/site-packages"))
            if matches:
                rel = matches[0].relative_to(self._venv).as_posix()
                self._venv_site_jail = f"{_VENV_JAIL_PATH}/{rel}"

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
            ValueError: If ``cmd`` is empty or the mode is unsupported.
            SecurityError: If ``cwd`` resolves outside ``work_root``, or
                ``cmd[0]`` is an absolute path that cannot be mapped into
                the jail.
            SandboxUnavailable: If namespace isolation cannot be
                established. The command is not executed in this case.
            FileNotFoundError: If ``cwd`` does not exist.
        """
        if not cmd:
            raise ValueError("cmd must be a non-empty list of program arguments")
        cmd = [str(part) for part in cmd]
        cwd_resolved = Path(cwd).resolve()
        if cwd_resolved != self._work_root and self._work_root not in cwd_resolved.parents:
            raise SecurityError(
                f"cwd {cwd_resolved} is outside the sandbox work root {self._work_root}"
            )
        if not cwd_resolved.is_dir():
            raise FileNotFoundError(f"sandbox cwd does not exist: {cwd_resolved}")
        if not isinstance(timeout_s, (int, float)) or timeout_s <= 0:
            timeout_s = 60

        jail_cmd = [self._remap_executable(cmd[0], cwd_resolved), *cmd[1:]]
        rel = cwd_resolved.relative_to(self._work_root)
        jail_cwd = "/work" if str(rel) == "." else "/work/" + rel.as_posix()
        return self._run_namespaced(jail_cmd, jail_cwd, float(timeout_s), self._scrubbed_env(env))

    # ------------------------------------------------------------------
    # command / environment mapping
    # ------------------------------------------------------------------
    def _remap_executable(self, prog: str, host_cwd: Path) -> str:
        """Map ``cmd[0]`` to the path it has *inside* the jail.

        Bare names (``"python3"``) are resolved via ``PATH`` after the
        pivot. Absolute paths (or ``a/b``-style relative paths) are
        resolved on the host and remapped: paths under ``work_root``
        become ``/work/...``; paths under the read-only system trees
        (``/usr``, reached via the ``/bin``→``usr/bin`` symlinks etc.)
        keep their literal path. Anything else cannot exist in the jail
        and is refused – executing a host binary from outside the jail
        would silently break isolation.
        """
        if "/" not in prog:
            return prog
        candidate = Path(prog) if os.path.isabs(prog) else host_cwd / prog
        real = Path(os.path.realpath(candidate))
        if real == self._work_root or self._work_root in real.parents:
            return "/work/" + real.relative_to(self._work_root).as_posix()
        if self._venv is not None and (real == self._venv or self._venv in real.parents):
            # Remap host venv paths to the read-only /venv mount in the jail.
            return _VENV_JAIL_PATH + "/" + real.relative_to(self._venv).as_posix()
        for prefix in _SYSTEM_JAIL_PREFIXES:
            pre = Path(prefix)
            if real == pre or pre in real.parents:
                return real.as_posix()
        raise SecurityError(
            f"command {prog!r} (resolves to {real}) is outside the sandbox jail: "
            "use a PATH-resolved name (e.g. 'python3') or a path inside work_root"
        )

    def _scrubbed_env(self, env: dict | None) -> dict[str, str]:
        """Build the child environment: allowlisted variables only."""
        child = {key: value for key, value in os.environ.items() if key in self.ENV_ALLOWLIST}
        if env:
            for key, value in env.items():
                if key in self.ENV_ALLOWLIST:
                    child[key] = str(value)
        # Inside the jail the work root is always /work. PATH is *forced*
        # (not setdefault): the host PATH is meaningless inside the jail
        # and must never leak in.
        child["HOME"] = "/work"
        if self._venv is not None:
            child["PATH"] = f"{_VENV_JAIL_PATH}/bin:{_JAIL_DEFAULT_PATH}"
            if self._venv_site_jail is not None:
                # Jail-relative site-packages so the venv's packages
                # (e.g. pytest) are importable without venv auto-detection.
                child["PYTHONPATH"] = self._venv_site_jail
        else:
            child["PATH"] = _JAIL_DEFAULT_PATH
        return child

    # ------------------------------------------------------------------
    # namespace backend
    # ------------------------------------------------------------------
    def _run_namespaced(
        self,
        cmd: list[str],
        jail_cwd: str,
        timeout_s: float,
        env: dict[str, str],
    ) -> ExecResult:
        """Fork the namespace helper and supervise it.

        Process tree::

            parent (this process)
            └── A: unshare(user,mount,pid,net) → fork B → waitpid → propagate status
                └── B: mounts → chroot → rlimits → setuid(65534) → exec target (PID 1)

        The parent pre-mounts the tmpfs new root (a fresh tmpfs cannot be
        used from inside the nested user namespace on this kernel), writes
        A's uid_map/gid_map after A unshares (the kernel only allows the
        *parent* user namespace to do that), then pumps stdout/stderr with
        a hard 1 MiB cap per stream and enforces the wall-clock timeout by
        killing the whole process group.
        """
        if not hasattr(os, "unshare"):
            raise SandboxUnavailable("os.unshare is not available on this platform")

        stage = tempfile.mkdtemp(prefix="casi-jail-")
        newroot = os.path.join(stage, "root")
        newroot_tmp = os.path.join(newroot, "tmp")
        # Parent-side tmpfs mounts (private; unmounted in the finally below).
        # These MUST be created here: a tmpfs mounted inside the nested user
        # namespace rejects file creation (EOVERFLOW) on this kernel.
        try:
            os.makedirs(newroot)
            _mount("tmpfs", newroot, "tmpfs", 0, f"size={_NEWROOT_TMPFS_SIZE},mode=755")
            _mount(None, newroot, None, _MS_PRIVATE, None)
            for sub in ("work", "usr", "etc", "tmp", "dev", "venv"):
                os.makedirs(os.path.join(newroot, sub))
            os.chmod(os.path.join(newroot, "etc"), 0o555)
            # makedirs honors the process umask (often 0077/0007 here); the
            # unprivileged target must traverse these, so force sane modes.
            # (etc is intentionally 555; work is chowned to the sandbox uid
            # later; usr/venv are covered by bind mounts.)
            os.chmod(os.path.join(newroot, "dev"), 0o755)
            _mount("tmpfs", newroot_tmp, "tmpfs", 0, f"size={_JAIL_TMP_SIZE},mode=1777")
            _mount(None, newroot_tmp, None, _MS_PRIVATE, None)
        except OSError as exc:
            _umount(newroot_tmp)
            _umount(newroot)
            shutil.rmtree(stage, ignore_errors=True)
            raise SandboxUnavailable(f"cannot prepare jail tmpfs mounts: {exc}") from exc

        out_r, out_w = os.pipe()
        err_r, err_w = os.pipe()
        go_r, go_w = os.pipe()  # parent -> A: "go" once id maps are installed
        status_r, status_w = os.pipe()  # child -> parent status lines (CLOEXEC)
        os.set_blocking(status_r, False)

        start = time.monotonic()
        pid: int | None = None
        try:
            try:
                pid = os.fork()
            except OSError as exc:
                raise SandboxUnavailable(f"cannot fork sandbox helper: {exc}") from exc

            if pid == 0:
                # Child A — never returns.
                self._child_a(
                    cmd, jail_cwd, timeout_s, env, newroot,
                    out_r, out_w, err_r, err_w, go_r, go_w, status_r, status_w,
                )
                os._exit(_SETUP_FAILED_CODE)  # unreachable

            # ---- parent ------------------------------------------------
            for fd in (out_w, err_w, status_w):
                os.close(fd)
            self._handshake(pid, go_r, go_w, status_r)
            return self._pump(pid, out_r, err_r, status_r, start, timeout_s)
        finally:
            if pid is not None:
                # Reap the helper if it is still around (never kill a
                # reaped pid: pids cannot be reused until reaped).
                try:
                    wpid, _ = os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    wpid = pid
                if wpid == 0:
                    _kill_group(pid)
                    try:
                        os.waitpid(pid, 0)
                    except ChildProcessError:
                        pass
            self._close_all(out_r, out_w, err_r, err_w, go_r, go_w, status_r, status_w)
            _umount(newroot_tmp)
            _umount(newroot)
            shutil.rmtree(stage, ignore_errors=True)

    @staticmethod
    def _close_all(*fds: int) -> None:
        for fd in fds:
            try:
                os.close(fd)
            except OSError:
                pass

    # -- handshake ------------------------------------------------------
    def _handshake(self, pid: int, go_r: int, go_w: int, status_r: int) -> None:
        """Run the setup handshake; raise SandboxUnavailable on any problem.

        Protocol on the status pipe (one line per message):
          A -> parent ``"ready"``  : unshare done, install id maps then send "go"
          A/B -> parent ``"err:<msg>"`` : setup failed, never ran
          B -> parent ``"ok"``     : jail built, exec is imminent
        """
        line = self._read_status_line(status_r, "waiting for sandbox helper")
        if line is None:
            _kill_group(pid)
            raise SandboxUnavailable("sandbox helper died before the namespace handshake")
        if line.startswith(_STATUS_ERR_PREFIX):
            _kill_group(pid)
            raise SandboxUnavailable(_decode_status_err(line))
        if line != _STATUS_READY.strip():
            _kill_group(pid)
            raise SandboxUnavailable(f"sandbox handshake: unexpected status {line!r}")
        self._install_id_maps(pid)
        try:
            os.write(go_w, b"go\n")
        except OSError as exc:
            _kill_group(pid)
            raise SandboxUnavailable(f"sandbox handshake: cannot signal helper: {exc}") from exc

        line = self._read_status_line(status_r, "waiting for jail setup")
        if line is None:
            _kill_group(pid)
            raise SandboxUnavailable("sandbox helper died during jail setup")
        if line.startswith(_STATUS_ERR_PREFIX):
            _kill_group(pid)
            raise SandboxUnavailable(_decode_status_err(line))
        if line != _STATUS_OK.strip():
            _kill_group(pid)
            raise SandboxUnavailable(f"sandbox handshake: unexpected status {line!r}")
        # "ok" received: the target is being exec'd; the status pipe will
        # hit EOF (CLOEXEC) once exec succeeds.

    @staticmethod
    def _read_status_line(status_r: int, what: str) -> bytes | None:
        """Read one ``\\n``-terminated status line; None on EOF/timeout."""
        sel = selectors.DefaultSelector()
        sel.register(status_r, selectors.EVENT_READ)
        buf = bytearray()
        try:
            deadline = time.monotonic() + _HANDSHAKE_TIMEOUT_S
            while True:
                if b"\n" in buf:
                    line, _, _ = bytes(buf).partition(b"\n")
                    return line
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SandboxUnavailable(f"sandbox handshake timed out {what}")
                if not sel.select(remaining):
                    raise SandboxUnavailable(f"sandbox handshake timed out {what}")
                try:
                    chunk = os.read(status_r, 4096)
                except OSError:
                    return None
                if not chunk:
                    # EOF: if we have a partial line, return it; else None.
                    return bytes(buf) or None
                buf.extend(chunk)
        finally:
            sel.close()

    @staticmethod
    def _install_id_maps(pid: int) -> None:
        """Write uid_map/gid_map for the helper (parent userns only)."""
        try:
            _write_file(f"/proc/{pid}/uid_map", f"0 0 1\n{_SANDBOX_UID} {_SANDBOX_UID} 1\n")
            _write_file(f"/proc/{pid}/gid_map", f"0 0 1\n{_SANDBOX_GID} {_SANDBOX_GID} 1\n")
        except OSError as exc:
            _kill_group(pid)
            raise SandboxUnavailable(f"cannot install uid/gid maps: {exc}") from exc

    # -- pump: capture output with hard caps, enforce timeout ------------
    def _pump(
        self,
        pid: int,
        out_r: int,
        err_r: int,
        status_r: int,
        start: float,
        timeout_s: float,
    ) -> ExecResult:
        os.set_blocking(out_r, False)
        os.set_blocking(err_r, False)
        sel = selectors.DefaultSelector()
        sel.register(out_r, selectors.EVENT_READ, "stdout")
        sel.register(err_r, selectors.EVENT_READ, "stderr")
        out = bytearray()
        err = bytearray()
        open_streams = {"stdout", "stderr"}
        timed_out = False
        truncated = False
        child_status: int | None = None
        drain_until: float | None = None
        deadline = start + timeout_s
        try:
            while True:
                now = time.monotonic()
                if child_status is None:
                    if not timed_out and now >= deadline:
                        _kill_group(pid)
                        timed_out = True
                    wpid, status = os.waitpid(pid, os.WNOHANG)
                    if wpid == pid:
                        child_status = status
                        drain_until = now + 2.0
                if truncated:
                    break
                if child_status is not None:
                    if not open_streams or (drain_until is not None and now >= drain_until):
                        break
                    wait = max(0.0, (drain_until or now) - now)
                elif timed_out:
                    wait = 0.5  # keep draining after the kill until EOF
                else:
                    # NB: a child's exit produces NO pipe event, so the select
                    # timeout must stay short; otherwise we would sit in
                    # epoll_wait past the child's death and misreport timeout.
                    wait = min(0.05, max(0.0, deadline - now))
                events = sel.select(wait)
                if not events:
                    continue
                for key, _ in events:
                    try:
                        data = os.read(key.fd, 65536)
                    except OSError:
                        data = b""
                    if not data:
                        sel.unregister(key.fd)
                        open_streams.discard(key.data)
                        continue
                    buf = out if key.data == "stdout" else err
                    buf.extend(data)
                    if len(buf) > _OUTPUT_CAP_BYTES:
                        del buf[_OUTPUT_CAP_BYTES:]
                        truncated = True
                        _kill_group(pid)
                        break
                if truncated:
                    break
        finally:
            sel.close()

        if child_status is None:
            _, child_status = os.waitpid(pid, 0)

        # A late "err:" (e.g. execvpe failing after "ok") must still refuse
        # to present a result as if the target ran.
        setup_err = self._drain_status_err(status_r)
        if setup_err:
            raise SandboxUnavailable(setup_err)

        if timed_out:
            returncode = -1
        elif os.WIFEXITED(child_status):
            returncode = os.WEXITSTATUS(child_status)
        elif os.WIFSIGNALED(child_status):
            returncode = -os.WTERMSIG(child_status)
        else:
            returncode = -1
        duration_s = time.monotonic() - start
        return ExecResult(
            returncode=returncode,
            stdout=out.decode("utf-8", "replace"),
            stderr=err.decode("utf-8", "replace"),
            timed_out=timed_out,
            duration_s=duration_s,
            truncated=truncated,
        )

    @staticmethod
    def _drain_status_err(status_r: int) -> str:
        """Return the setup error message if the child reported one, else ''."""
        chunks = []
        while True:
            try:
                data = os.read(status_r, 65536)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
        for line in b"".join(chunks).split(b"\n"):
            if line.startswith(_STATUS_ERR_PREFIX):
                return _decode_status_err(line)
        return ""

    # ------------------------------------------------------------------
    # child A: namespaces + fork B, propagate B's status
    # ------------------------------------------------------------------
    def _child_a(
        self,
        cmd: list[str],
        jail_cwd: str,
        timeout_s: float,
        env: dict[str, str],
        newroot: str,
        out_r: int, out_w: int,
        err_r: int, err_w: int,
        go_r: int, go_w: int,
        status_r: int, status_w: int,
    ) -> None:
        """Never returns: exits via os._exit."""
        try:
            # A keeps: go_r, go_w, status_w, out_w, err_w.
            self._close_all(out_r, err_r, status_r)
            os.setsid()  # new process group: parent can killpg the whole tree
            try:
                os.unshare(
                    os.CLONE_NEWUSER | os.CLONE_NEWNS | os.CLONE_NEWPID | os.CLONE_NEWNET
                )
            except OSError as exc:
                _fail(status_w, f"unshare(CLONE_NEWUSER|NEWNS|NEWPID|NEWNET) failed: {exc}")
            try:
                os.write(status_w, _STATUS_READY)
            except OSError as exc:
                os._exit(_SETUP_FAILED_CODE)
            # Wait for the parent to install the id maps.
            sel = selectors.DefaultSelector()
            sel.register(go_r, selectors.EVENT_READ)
            try:
                if not sel.select(_HANDSHAKE_TIMEOUT_S):
                    _fail(status_w, "parent did not install uid/gid maps in time")
            finally:
                sel.close()
            # Privatize mounts so the jail setup never propagates to the host.
            try:
                _mount(None, "/", None, _MS_REC | _MS_PRIVATE, None)
            except OSError as exc:
                _fail(status_w, f"cannot privatize mounts: {exc}")

            bpid = os.fork()
            if bpid == 0:
                self._child_b(
                    cmd, jail_cwd, timeout_s, env, newroot,
                    out_w, err_w, go_r, go_w, status_w,
                )
                os._exit(_SETUP_FAILED_CODE)  # unreachable
            # B has its own copies now; drop ours so the parent sees EOF.
            self._close_all(out_w, err_w, go_r, go_w, status_w)
            while True:
                _, status = os.waitpid(bpid, 0)
                if os.WIFEXITED(status):
                    os._exit(os.WEXITSTATUS(status))
                if os.WIFSIGNALED(status):
                    sig = os.WTERMSIG(status)
                    signal.signal(sig, signal.SIG_DFL)
                    os.kill(os.getpid(), sig)
                    os._exit(_SETUP_FAILED_CODE)
        except Exception as exc:  # noqa: BLE001 - child must never return
            try:
                _fail(status_w, f"sandbox helper failed: {exc!r}")
            except Exception:
                os._exit(_SETUP_FAILED_CODE)

    # ------------------------------------------------------------------
    # child B: build the jail, drop privileges, exec (becomes PID 1)
    # ------------------------------------------------------------------
    def _child_b(
        self,
        cmd: list[str],
        jail_cwd: str,
        timeout_s: float,
        env: dict[str, str],
        newroot: str,
        out_w: int,
        err_w: int,
        go_r: int,
        go_w: int,
        status_w: int,
    ) -> None:
        """Never returns: execs the target or exits via os._exit."""
        try:
            self._close_all(go_r, go_w)
            devnull = os.open("/dev/null", os.O_RDONLY)
            os.dup2(devnull, 0)
            os.close(devnull)
            os.dup2(out_w, 1)
            os.dup2(err_w, 2)
            # status_w is CLOEXEC: a successful exec closes it.
            self._build_jail(newroot)
            self._apply_rlimits(timeout_s)
            # Make the jailed cwd (and its parents up to /work) owned by the
            # sandbox uid: the work root's subdirectories are typically
            # root-owned and would otherwise deny chdir/write to the
            # unprivileged target.
            target = jail_cwd
            while True:
                os.chown(target, _SANDBOX_UID, _SANDBOX_GID)
                if target == "/work":
                    break
                parent = os.path.dirname(target)
                if parent == target:
                    break
                target = parent
            # Irreversibly drop privileges before exec.
            os.setgroups([])
            os.setresgid(_SANDBOX_GID, _SANDBOX_GID, _SANDBOX_GID)
            os.setresuid(_SANDBOX_UID, _SANDBOX_UID, _SANDBOX_UID)
            os.chdir(jail_cwd)
            try:
                os.write(status_w, _STATUS_OK)
            except OSError:
                pass
            os.execvpe(cmd[0], cmd, env)
        except Exception as exc:  # noqa: BLE001 - child must never return
            try:
                _fail(status_w, f"jail setup failed: {exc!r}")
            except Exception:
                os._exit(_SETUP_FAILED_CODE)

    def _build_jail(self, newroot: str) -> None:
        """Assemble the new root and chroot into it. Runs as inner-root."""
        # Mirror the host's merged-/usr layout with symlinks.
        for link, target in (
            ("bin", "usr/bin"),
            ("lib", "usr/lib"),
            ("lib64", "usr/lib64"),
            ("sbin", "usr/sbin"),
        ):
            os.symlink(target, os.path.join(newroot, link))
        # Read-only userspace. (No /proc: a fresh proc mount is rejected
        # in the nested user namespace here, and the host's would leak PIDs.
        # /etc is a plain empty dir, mode 555, so passwd/shadow are absent.)
        _bind_ro("/usr", os.path.join(newroot, "usr"))
        # Expose the host venv read-only so jailed commands can use the
        # same interpreter/packages (e.g. pytest). Disabled when no venv
        # was detected (CASI_SANDBOX_VENV="" also disables).
        if self._venv is not None:
            _bind_ro(str(self._venv), os.path.join(newroot, "venv"))
        # A few device nodes (bind only; remount-ro is rejected on this
        # kernel, but these nodes are inherently harmless).
        for node in ("null", "zero", "urandom"):
            placeholder = os.path.join(newroot, "dev", node)
            open(placeholder, "w").close()
            _mount(os.path.join("/dev", node), placeholder, None, _MS_BIND, None)
        # The ONLY read-write host directory visible in the jail.
        work_dst = os.path.join(newroot, "work")
        _mount(str(self._work_root), work_dst, None, _MS_BIND, None)
        os.chown(work_dst, _SANDBOX_UID, _SANDBOX_GID)

        # NOTE: pivot_root(2) is rejected with EINVAL inside the nested user
        # namespace on this kernel when the new root's superblock belongs to
        # the parent user namespace, so we chroot(2) instead. This is safe
        # here: the process holds no directory fds to the outside, it drops
        # to the unprivileged sandbox uid right after (so it cannot mount,
        # mknod, or re-chroot its way out), and /proc is not mounted.
        os.chroot(newroot)
        os.chdir("/")

    @staticmethod
    def _apply_rlimits(timeout_s: float) -> None:
        cpu = int(timeout_s) + _CPU_GRACE_S
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_AS, (_AS_LIMIT_BYTES, _AS_LIMIT_BYTES))
        resource.setrlimit(resource.RLIMIT_NPROC, (_NPROC_LIMIT, _NPROC_LIMIT))
        resource.setrlimit(resource.RLIMIT_FSIZE, (_FSIZE_LIMIT_BYTES, _FSIZE_LIMIT_BYTES))


def _decode_status_err(line: bytes) -> str:
    assert line.startswith(_STATUS_ERR_PREFIX)
    return line[len(_STATUS_ERR_PREFIX):].decode("utf-8", "replace").strip()
