# CASI — Technical Roadmap

**Date:** 2026-10-08 · Milestone 1 covers **Phase 1 + Phase 2**.

## Phase 1 — Audit, spec, roadmap, dev environment ✅ (this milestone)
- [x] Repository audit → `ARCHITECTURE_GAP_ANALYSIS.md`
- [x] Architecture specification → `ARCHITECTURE.md`
- [x] Technical roadmap → this file
- [x] Dev environment: `requirements.txt`, `Dockerfile`, `docker-compose.yml`, `.env.example`, `SETUP.md`

## Phase 2 — Core platform ✅ (this milestone)
- [ ] AI Kernel (`casi/kernel.py`): goal lifecycle, component wiring, budgets
- [ ] Planner (`casi/planner.py`): rule-based software-goal decomposition → DAG `Plan`
- [ ] DAG scheduler (`casi/scheduler.py`): validation, topological levels, parallel execution, retries, checkpoints, cancellation
- [ ] Agent runtime (`casi/agents/`): registry, supervisor, coder, tester, debugger, reviewer, researcher
- [ ] Memory (`casi/memory/`): working + long-term (TF-IDF recall, JSONL)
- [ ] Permissions + approvals + audit (`casi/security/`)
- [ ] Execution sandbox (`casi/execution/`)
- [ ] Model layer (`casi/models/`): provider ABC, mock, optional Ollama, router, cost tracker
- [ ] Workspace filesystem (`casi/filesystem/`)
- [ ] Plugin interfaces + multimodal/robotics stubs (`casi/integrations/`)
- [ ] FastAPI surface (`casi/api/`)
- [ ] Milestone demo (`scripts/run_milestone_demo.py`) — acceptance flow from `ARCHITECTURE.md` §4
- [ ] Test suite green, bandit scan, `LIMITATIONS.md`

**Exit criteria:** the acceptance demo runs end-to-end for real (plan → agents → sandboxed execution → test → failure → repair → verified artifact → approval gate pauses before publish), `pytest` passes, bandit findings are triaged.

## Phase 3 — AI OS web interface (next milestone)
- Dashboard: chat, goal list, task DAG visualization, agent activity stream, approval queue, log viewer, artifact browser
- WebSocket/SSE streaming of execution events
- Goal templates and saved workflows

## Phase 4 — Autonomous software engineering
- Multi-file project scaffolding, dependency installation in sandbox
- Research workflows (web fetch via allowlisted plugin), data-analysis agent
- Artifact packaging (zip/tar), version diffs, rollback

## Phase 5 — Model routing, observability, hardening
- Real provider integrations (API keys via env only), eval harness for routing decisions
- Prometheus metrics, structured tracing, cost budgets with hard caps
- Security review: sandbox escape tests, dependency audit, fuzz the approval flow

## Phase 6 — Desktop integration & computer automation
- OS-level automation behind explicit per-action approval; accessibility-API based control
- Screen/element grounding (human-verified), action replay

## Phase 7 — Voice, multimodal, mobile
- Speech-to-text/text-to-speech behind the plugin interface; image understanding via provider plugins; mobile companion app (API client)

## Phase 8 — Robotics
- Perception/planning/simulation interfaces (stubs exist from Phase 2); hardware adapters as opt-in plugins; simulation-first validation before any physical execution
