# CASI — Security Architecture

**Status:** Milestone 2 security hardening — COMPLETED (verified 2026-10-08).
**Labels:** `COMPLETED` = verified working on this VM · `SIMPLIFIED` = works, not production-grade ·
`FUTURE` = not implemented · `BLOCKED` = cannot proceed yet.

## 1. Threat model

| Element | Trust status | Rationale |
|---|---|---|
| The kernel, agents, API server, and their code | **Trusted** | Runs as the host user; CASI does not defend against its own process being compromised. |
| Host `/usr` (bind-mounted read-only into the jail) | **Trusted** | The sandbox reuses host userspace. A compromised host binary compromises the jail. |
| Code executed by agents (generated or under test) | **Untrusted** | Always runs inside the namespace sandbox (`casi/execution/sandbox.py`). |
| Files produced by untrusted runs (in `/work`) | **Untrusted input** | Read-write by design; treat as hostile when consumed later. |
| API clients | **Untrusted until authed** | Key → role; capabilities gate every endpoint; deny-by-default. |
| Approvals stored in `ApprovalGate` | **Untrusted operator input** | In-memory only; a restart loses pending approvals (see §7). |
| Kernel (Linux) itself | **Trusted** | The sandbox is containment, not a boundary against kernel exploits. |

What is *out of scope*: kernel privilege-escalation exploits, CPU side
channels, physical access, and malicious operators with valid ADMIN keys.
See §7 Residual risks.

## 2. Sandbox design (`casi/execution/sandbox.py`) — COMPLETED

Every agent-executed command runs in a fresh set of Linux namespaces,
built with `os.fork()` + `os.unshare()` in the **main thread** (fork-based
setup requires it). There is no host-execution path: `CASI_SANDBOX_MODE`
accepts only `"strict"`, and *any* isolation failure raises
`SandboxUnavailable` — the command is never run on the host. The
pre-Milestone-2 Docker backend was removed (could not be exercised on this
VM; container runtime is outside the dependency set).

### 2.1 Jail layout

```
HOST (outer user: your UID)
│
├─ os.fork() → child calls unshare(CLONE_NEWUSER | NEWNS | NEWPID | NEWNET)
│
└─ CHILD (inside new namespaces)
   ├─ User ns: uid map written by parent (outer UID → 0, 65534 → 65534)
   ├─ Mount ns: tmpfs mounted as new root (256 MiB)
   │   ├─ /usr          ← host /usr bind-mounted READ-ONLY
   │   ├─ /etc          ← empty, root-owned, unwritable (no /etc/passwd, no resolver)
   │   ├─ /dev          ← null, zero, urandom ONLY
   │   ├─ /tmp          ← private tmpfs (64 MiB)
   │   ├─ /venv         ← host venv bind-mounted READ-ONLY, when usable (see §2.4)
   │   └─ /work         ← ONLY read-write bind-mount (the goal's work_root)
   ├─ chroot(new root)          # pivot_root rejected by this kernel in nested userns (EINVAL);
   │                             # chroot is safe here: no outside dir fds + irreversible uid drop
   ├─ setresuid/setresgid(65534, 65534) + setgroups([])   # IRREVERSIBLE; target is never root
   ├─ PID ns: target becomes PID 1; sees no host processes; exit tears the ns down
   ├─ NET ns: no interface brought up — not even loopback → ALL egress fails (incl. 127.0.0.1)
   └─ RLIMITs: CPU (timeout+5s grace), AS 1 GiB, NPROC 32, FSIZE 64 MiB per file
```

**I/O policy:** stdout/stderr are captured through pipes with a **1 MiB
hard cap per stream** — exceeding it SIGKILLs the whole process group and
flags `truncated=True`. A wall-clock timeout kills the process group and
sets `timed_out=True`.

**Environment scrub:** the jail env is rebuilt from scratch
(`PATH`→`/venv/bin:/usr/local/bin:/usr/bin:/bin` when the venv is
exposed, else `/usr/local/bin:/usr/bin:/bin`; `HOME=/nonexistent`, plus a
minimal safe set). Host secrets never leak in by env.

**Host side effects (documented, bounded):** `work_root` and the
directories on the `work_root`→`cwd` path are `chown`-ed to 65534 so the
target can `chdir`/write there; file contents and ownership below those
directories are untouched. A per-run staging dir under the system temp dir
holds the two tmpfs mounts and is removed afterwards. All jail mounts are
private to the new mount namespace and never propagate to the host.

### 2.2 Hardening checklist

| Control | Status | Evidence |
|---|---|---|
| No network (net ns, loopback down) | COMPLETED | test: egress incl. 127.0.0.1 fails |
| Non-root target (uid/gid 65534, irreversible) | COMPLETED | test: `os.getuid() != 0` |
| Read-only FS except `/work` | COMPLETED | tests: writes outside `/work` fail |
| `..` traversal contained | COMPLETED | test: `cwd` escape → `SecurityError` |
| rlimits (CPU/AS/NPROC/FSIZE) | COMPLETED | tests: fork-bomb, huge malloc contained |
| Output caps (1 MiB/stream, SIGKILL + `truncated=True`) | COMPLETED | test: huge output contained |
| Wall-clock timeout + process-group SIGKILL | COMPLETED | test: timeout kills |
| Env scrub (no secret inheritance) | COMPLETED | test: hostile env vars absent in jail |
| Fail closed on *any* isolation failure | COMPLETED | code + test: `SandboxUnavailable`, no host fallback |
| `CASI_SANDBOX_MODE` pinned to `"strict"` | COMPLETED | any other value → `ValueError` |
| seccomp-bpf syscall filter | **FUTURE** | not applied; containment rests on namespaces + uid drop + rlimits |

31 sandbox tests green, including: `/etc/passwd` absent, pid 1,
non-root, fork-bomb / huge-output / huge-malloc contained, timeout kills,
`../` contained, network egress fails.

### 2.3 Known sandbox caveats

- **Fork requires the main thread.** The kernel never calls the sandbox
  from worker threads (documented; thread-based callers get
  `SandboxUnavailable` rather than a degraded path).
- **btrfs + unprivileged userns quirk** (verified 2026-10-08 on this VM):
  the filesystem denies uid-65534 access inside the jail regardless of
  permission bits. The venv pre-flight probe (§2.4) detects this and skips
  venv exposure; the sandbox still runs fully namespaced.
- **umask/world-readability:** jailed commands run as uid 65534, so any
  host file they must read (site-packages installed as root, test data)
  needs world-readable bits. After `pip install` as root outside a venv,
  run `chmod -R a+rX <site-packages>` — see SETUP.md §7.

### 2.4 Venv exposure

When the CASI process runs inside a virtualenv (or `CASI_SANDBOX_VENV`
names one), the venv is bind-mounted **read-only** at `/venv` and
`/venv/bin` is prepended to the jail `PATH`, so jailed commands use the
same interpreter and packages (e.g. `pytest`). The candidate is pre-flight
probed for readability by the sandbox uid; on hostile filesystems (btrfs
on this VM) it is skipped with a `RuntimeWarning` and the jail falls back
to the system python. The venv is **trusted host software**, not
untrusted input.

## 3. API authentication — COMPLETED

- `CASI_REQUIRE_AUTH`, `CASI_HOST` (default `127.0.0.1`), `CASI_PORT`;
  `Settings.validate_deployment()` **fail-fasts with
  `InsecureDeploymentError`** when the bind address is non-loopback **or**
  `CASI_REQUIRE_AUTH=1`, unless `CASI_API_KEY` is ≥ 32 chars and not a
  placeholder. Loopback without a key starts but prints a loud warning.
- Key → role: `CASI_API_KEY` → ADMIN, `CASI_OPERATOR_API_KEY` → OPERATOR,
  `CASI_VIEWER_API_KEY` → VIEWER. Unknown/missing key → **401**.
- Per-endpoint capability gates (`require_capability` dependency in
  `casi/api/app.py`): `POST /goals`, `/run`, `/cancel` → `MANAGE_GOALS`
  (ADMIN+OPERATOR); `POST /approvals/{id}/resolve` → `APPROVE_PUBLISH`
  (ADMIN only); all `GET`s → `READ_WORKSPACE`. A key without the
  capability → **403**.

### 3.1 Deployment matrix

| Deployment | Auth state | Gate |
|---|---|---|
| `CASI_HOST=127.0.0.1`, no key | Allowed, loud warning | dev only; loopback-only exposure |
| `CASI_HOST=127.0.0.1`, strong key | Allowed | recommended dev/test |
| `CASI_HOST=0.0.0.0` (or any non-loopback) + strong key | Allowed | minimum for any network exposure |
| Non-loopback **without** strong key | **Refused at startup** (`InsecureDeploymentError`) | — |
| `CASI_REQUIRE_AUTH=1` without strong key | **Refused at startup** | even on loopback |

A "strong key" means ≥ 32 chars and not a placeholder
(`looks_like_placeholder` rejects `changeme`, `password`, `test`,
repeated single chars, etc.). Note `0.0.0.0` is deliberately **not**
treated as loopback.

## 4. Approval-gate integrity — COMPLETED

- New capabilities: `APPROVE_OWN_RUNS` (ADMIN **only**) and `MANAGE_GOALS`
  (ADMIN+OPERATOR). `APPROVE_OWN_RUNS` exists so auto-approve can be
  expressed as a capability, never as a silent default.
- `kernel.run_goal(..., auto_approve=True)` requires **both** the explicit
  flag **and** a role holding `APPROVE_OWN_RUNS`, checked *before any
  work starts* (`_require_auto_approve`). A bypass attempt raises
  `PermissionDenied` and is audited as `approval.bypass_denied`.
- Every exercised auto-approve resolves through `_auto_resolve_approval`
  (defense-in-depth re-check) and writes a mandatory
  `approval.auto_approved` audit record with approval id, action, and
  actor role.
- `ApprovalGate` holds records behind a `threading.Condition`; `request`
  → `wait` → `resolve` from another thread. A rejected approval surfaces
  as `PermissionDenied` — downstream fails closed. The gate accepts an
  `audit_hook`; the kernel wires it to the `AuditLog` so every
  `approval.requested` / `approval.resolved` transition lands in the
  audit trail.
- The demo's `--auto-approve` goes through the same explicit
  `role=Role.ADMIN` path — there is no ambient "I am the operator"
  shortcut.

## 5. Agent permission model — COMPLETED

`AgentContext.role` is set by the kernel's ctx factory; `require_capability()`
in `casi/agents/base.py` is the single choke point (lazy-imports
`casi.security.permissions` so agent modules stay decoupled at import
time). Per-agent gates (verified by tests):

| Agent | Required capabilities |
|---|---|
| coder | `WRITE_WORKSPACE` |
| tester | `EXECUTE_CODE` (+ `WRITE_WORKSPACE` where it writes reports) |
| debugger | `READ_WORKSPACE` (diagnose), `WRITE_WORKSPACE` (patch) |
| reviewer | `READ_WORKSPACE` |
| researcher | `READ_WORKSPACE` |
| planner_agent | `WRITE_WORKSPACE` |

`role=None` is the legacy ungated mode (documented, used only when the
kernel is constructed without a role). Deny-by-default: unknown roles and
ungranted capabilities raise `PermissionDenied`.

## 6. Audit logging — SIMPLIFIED

`AuditLog` appends JSONL records (`event`, `goal_id`, `task_id`,
`actor`, `details`, timestamp) to `<data_dir>/audit.jsonl`. Every goal,
task, tool call, test run, patch, approval transition, and cost event is
recorded. **Simplified:** the log is append-only by convention, not by
cryptographic guarantee — no signatures, no tamper-evidence, no remote
shipping, no rotation. Good enough for a single-operator dev system; not a
compliance audit trail.

## 7. Secrets handling — COMPLETED (SIMPLIFIED store)

- All secrets come from the environment (`CASI_API_KEY`,
  `CASI_OPERATOR_API_KEY`, `CASI_VIEWER_API_KEY`, `NEBIUS_API_KEY`,
  `CASI_RAG_API_KEY`); `NEBIUS_API_KEY` is read **at call time**, never
  cached in config. No keys, tokens, or credentials live in the repo
  (`.env.example` holds placeholders only; `.env` is git-ignored).
- API keys are compared as plain strings and held in process memory.
  There is no hashing, no rotation API, and no encrypted secrets store —
  acceptable for a single-tenant dev system, listed as a residual risk.

## 8. Residual risks (acknowledged, not fixed)

1. **Kernel exploits.** The jail shares the host kernel; a kernel
   privesc defeats all namespace isolation. This sandbox is *containment
   for untrusted code*, not a security boundary against a skilled
   attacker.
2. **Side channels.** Timing, CPU cache, memory-pressure — unmitigated.
3. **Per-run resource bounds only.** A wasteful-but-legal run can still
   burn 1 GiB / 32 processes / its CPU slice while alive; no global
   scheduler budget exists.
4. **Trusted host userspace.** `/usr` is bind-mounted read-only and
   trusted; a malicious host binary poisons the jail.
5. **Exfiltration via `/work`.** The work dir is intentionally
   read-write and persists on the host; treat agent-produced files as
   untrusted input.
6. **No seccomp-bpf.** Syscall filtering is future work.
7. **Single-tenant keys.** No per-user identity, no rotation, no TLS
   termination in-app. Put a reverse proxy with TLS in front for any
   network deployment.
8. **In-memory approvals.** Pending approvals die with the process; a
   crash loses the approval queue (checkpoints resume *goals* at level
   granularity, not approvals).
9. **Append-only-by-convention audit log.** No tamper evidence.
10. **btrfs userns quirk.** On btrfs, venv exposure is skipped (the
    sandbox itself still works); see §2.3.
