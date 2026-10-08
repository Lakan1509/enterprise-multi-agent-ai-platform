# CASI — Technical Roadmap

**Date:** 2026-10-08 · **Status:** Milestone 2 (security hardening) complete.

Phase statuses: `DONE` = verified on this VM · `PLANNED` = not started.
See LIMITATIONS.md for what is mock/simplified vs real.

## Phase 1 — Audit, spec, roadmap, dev environment ✅ DONE (Milestone 1)
- [x] Repository audit → `ARCHITECTURE_GAP_ANALYSIS.md`
- [x] Architecture specification → `ARCHITECTURE.md`
- [x] Technical roadmap → this file
- [x] Dev environment: `requirements.txt`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `SETUP.md`

## Phase 2 — Core platform ✅ DONE (Milestone 1)
- [x] AI Kernel (`casi/kernel.py`): goal lifecycle, component wiring
- [x] Planner (`casi/planner.py`): rule-based software-goal decomposition → DAG `Plan`
- [x] DAG scheduler (`casi/scheduler.py`): validation, topological levels, parallel execution, retries, checkpoints, cancellation, partial-failure semantics, crash-resume
- [x] Agent runtime (`casi/agents/`): registry, supervisor, coder, tester, debugger, reviewer, researcher, planner_agent — all 7 rule-based, all verified end-to-end
- [x] Memory (`casi/memory/`): working + long-term (TF-IDF recall, JSONL, cross-process persistence)
- [x] Permissions + approvals + audit (`casi/security/`)
- [x] Execution sandbox (`casi/execution/`): Milestone-1 hardened subprocess → **Milestone-2 namespace jail**
- [x] Model layer (`casi/models/`): provider ABC, mock, optional Ollama, **new Nebius**, router, cost tracker, error taxonomy
- [x] Workspace filesystem (`casi/filesystem/`)
- [x] Plugin interfaces + multimodal/robotics stubs (`casi/integrations/`)
- [x] FastAPI surface (`casi/api/`)
- [x] Milestone demo (`scripts/run_milestone_demo.py`) — acceptance flow from `ARCHITECTURE.md` §7
- [x] Test suite: **273 passed, 0 failed** (277 collected, 2026-10-08), `LIMITATIONS.md`

**Exit criteria (met):** the acceptance demo runs end-to-end for real
(plan → agents → sandboxed execution → test → failure → repair → verified
artifact → approval gate pauses before publish), `pytest` is green,
findings triaged.

## Milestone 2 — Security hardening ✅ DONE (2026-10-08)

Delivered on top of Phase 2; not a separate phase:

- [x] **Namespace sandbox** (`casi/execution/sandbox.py`): user/mount/pid/net
  namespaces via `os.fork`+`unshare`; tmpfs new root; host `/usr` RO;
  empty `/etc`; `/dev` null/zero/urandom only; only `work_root` RW at
  `/work`; `chroot` (pivot_root rejected by this kernel); irreversible
  drop to uid/gid 65534; **no network** (loopback down); rlimits
  (CPU/AS 1 GiB/NPROC 32/FSIZE 64 MiB); wall-clock timeout + SIGKILL
  process group; 1 MiB/stream output cap; `SandboxUnavailable` on **any**
  isolation failure — no host fallback; `CASI_SANDBOX_MODE=strict` only.
  31 tests: /etc/passwd absent, `..` contained, egress fails, pid 1,
  non-root, fork-bomb/huge-output/huge-malloc contained, timeout kills.
  Venv exposure: host venv RO at `/venv` when usable (pre-flight probe;
  btrfs on this VM denies unprivileged userns access → skipped with warning).
- [x] **Auth hardening** (`casi/config.py`, `casi/api/app.py`,
  `casi/security/auth.py`): `CASI_REQUIRE_AUTH`, `CASI_HOST` (default
  `127.0.0.1`); `validate_deployment()` fail-fasts with
  `InsecureDeploymentError` on non-loopback bind or `CASI_REQUIRE_AUTH=1`
  unless key ≥ 32 chars and not placeholder; loud warning on loopback
  without key. Key→role (ADMIN/OPERATOR/VIEWER); per-endpoint capability
  gates (401/403 semantics).
- [x] **Approval integrity** (`casi/security/approvals.py`,
  `permissions.py`, `kernel.py`): `APPROVE_OWN_RUNS` (ADMIN only) +
  `MANAGE_GOALS`; auto-approve needs explicit flag **and** capable role,
  checked before any work; bypass → `PermissionDenied` + audited
  `approval.bypass_denied`; every auto-approve → `approval.auto_approved`
  record; `ApprovalGate` audit_hook wired to `AuditLog`.
- [x] **Agent permissions**: `AgentContext.role` + `require_capability()`
  choke point; per-agent gates (coder→WRITE, tester→EXECUTE, …).
- [x] **Model providers**: error taxonomy; Ollama hardened (timeouts,
  retries, env config); **Nebius** provider (OpenAI-compatible, key from
  env at call time); REAL Ollama 0.40.1 smoke test green
  (`evidence/llm_smoke.log`, qwen2.5:0.5b); Nebius 401-mapping verified,
  completion unverified (no key).
- [x] **Docs**: `SECURITY.md` (threat model, jail design, deployment
  matrix), `ARCHITECTURE.md` refreshed, `ROADMAP.md`/`SETUP.md`/
  `LIMITATIONS.md` rewritten honestly, `PHASE3_PLAN.md` drafted.

## Phase 3 — AI OS web interface 🔜 PLANNED (next)

Web dashboard over the existing FastAPI backend. Full plan in
`PHASE3_PLAN.md` (stack decision: **React+Vite+Tailwind served statically
+ existing FastAPI**, SSE for real-time logs/monitoring). Sub-milestones:
3.1 read-only dashboard (goals/plans/tasks/artifacts/logs) →
3.2 approval queue + interactive run controls →
3.3 DAG visualization + artifact browser →
3.4 polish, auth story, hardening.
Honest scope: this is a *local-operator console*, not a multi-tenant
SaaS UI — single-tenant API keys are reused, no new identity system.

## Phase 4 — Autonomous software engineering 🔜 PLANNED

- Multi-file project scaffolding, dependency installation inside the sandbox
- Research workflows (web fetch via allowlisted plugin), data-analysis agent
- Artifact packaging (zip/tar), version diffs, rollback
- Scope note: depends on Phase 3 only for UX; sandbox already supports it.
  The LLM provider story must mature first — rule-based agents will not
  carry real multi-file engineering.

## Phase 5 — Model routing, observability, hardening 🔜 PLANNED

- Real provider integrations (API keys via env only), eval harness for routing decisions
- Prometheus metrics, structured tracing, cost budgets with hard caps
- Security review: sandbox escape tests, dependency audit, fuzz the approval flow,
  seccomp-bpf syscall filter for the jail (currently unimplemented)
- Scope note: Nebius completion still unverified live; needs a key + budget.

## Phase 6 — Desktop integration & computer automation 🔜 PLANNED

- OS-level automation behind explicit per-action approval; accessibility-API based control
- Screen/element grounding (human-verified), action replay
- Scope note: explicit permissions + sandboxing + audit from Milestones 1–2
  are the prerequisite substrate; no automation runs without a capable role
  and an approval gate. Cross-platform installers; never claim a platform
  until tested on it.

## Phase 7 — Voice, multimodal, mobile 🔜 PLANNED

- Speech-to-text/text-to-speech behind the plugin interface; image understanding via provider plugins; mobile companion app (API client)
- Scope note: `MultimodalPlugin` is a `NotImplementedError` stub today;
  provider plugins must exist before any of this is real.

## Phase 8 — Robotics 🔜 PLANNED

- Perception/planning/simulation interfaces (stubs exist from Phase 2); hardware adapters as opt-in plugins; simulation-first validation before any physical execution
- Scope note: furthest out; `RoboticsPlugin.execute` raises
  `NotImplementedError` today and stays that way until a simulator-backed
  adapter exists.
