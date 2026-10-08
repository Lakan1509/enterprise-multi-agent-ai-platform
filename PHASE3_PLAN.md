# CASI — Phase 3 Plan: Web Dashboard

**Status:** PLANNED · **Date:** 2026-10-08
**Goal:** a local-operator console for CASI — chat, goals, task DAG,
agent activity, approval queue, logs, artifacts — over the existing
FastAPI backend. This is a *single-operator dev console*, not a
multi-tenant SaaS UI.

## 1. Stack decision — DECIDED

**React + Vite + Tailwind CSS frontend, built to static files and served
by the existing FastAPI app** (`StaticFiles` mount), with the FastAPI
backend unchanged in role (it stays the system of record).

Why this, vs the alternatives:

| Option | Verdict | Reason |
|---|---|---|
| **React+Vite+Tailwind, static build served by FastAPI** | ✅ chosen | Real interactive UI (DAG graph, streaming logs, approval actions) without a second server process or port. Build output is static assets — the deployment story stays "one `uvicorn` process". Component ecosystem ( DAG rendering, virtualized log lists) beats the alternatives for the views we need. |
| Streamlit | ❌ rejected | Fast to prototype, but its rerun-execution model fights the streaming/DAG interactions we want, it needs its own server port/process, theming an operator console in it is painful, and it drags a heavy dependency tree into the runtime. |
| htmx + server templates | ❌ rejected | Excellent for document-style apps, weak for the two hard views: an interactive task-DAG graph and a live multi-stream log viewer. We'd end up writing as much JS as React anyway, without its tooling. |

The frontend is a **build artifact**, not a second service: `web/` source
lives in the repo, `web/dist/` (git-ignored, built in CI/release) is
mounted by FastAPI at `/`. No Node in the runtime dependency set —
only at build time.

## 2. API surface: what exists, what Phase 3 needs

### Already exist (verified in `casi/api/app.py`) — no changes needed

`GET /health` · `POST /goals` · `GET /goals` · `GET /goals/{id}` ·
`POST /goals/{id}/run` · `GET /goals/{id}/plan` ·
`POST /goals/{id}/cancel` · `GET /agents` · `GET /artifacts?goal_id=` ·
`GET /approvals/pending` · `POST /approvals/{id}/resolve` ·
`GET /logs?goal_id=&limit=`

### New endpoints needed (all PLANNED)

| Endpoint | Purpose | Notes |
|---|---|---|
| `GET /goals/{id}/tasks` | Task outcomes for the DAG view | Derive from `RunReport.outcomes`; today only the plan + final report exist — add a per-goal task-status projection |
| `GET /goals/{id}/artifacts/tree` | Artifact file tree for the browser | `list_artifacts` is flat; add tree + `GET /artifacts/content?path=` (jailed read, size-capped, READ_WORKSPACE) |
| `GET /goals/{id}/events` (SSE) | Real-time stream: task transitions, test output, approvals | See §4 |
| `GET /goals/{id}/logs/stream` (SSE) | Live audit-log tail | See §4; alternatively one multiplexed `/events` stream (OPEN, §8) |
| `POST /goals/{id}/approvals` | Request a manual approval from the UI | Thin wrapper over `ApprovalGate.request` (MANAGE_GOALS) |
| `GET /config` | Read-only deployment info (provider, sandbox mode, auth-required flag) | Never expose key material |

## 3. Data model (frontend view of backend records)

```
Goal        { id, text, status, created_at, plan?: Plan, report?: RunReport, artifacts: [path] }
TaskSpec    { id, name, description, agent_capability, depends_on, approval_required, params }
TaskOutcome { task_id, status: ok|failed|skipped|cancelled, attempts, error?, result? }
Approval    { id, action, details, status: pending|approved|rejected, created_at, resolved_at?, note }
LogEntry    { event, goal_id, task_id, actor, details, timestamp }
Agent       { name, capabilities }
```

The backend already produces all of these (`kernel.Goal`,
`planner.Plan/TaskSpec`, `scheduler.TaskOutcome`, `approvals.Approval`,
`audit` JSONL, `agents` registry) — Phase 3 adds *projections*
(task-status rollup, artifact tree), not a new store. Persistence stays
exactly what it is today (JSONL + workspace files); no database.

## 4. Real-time transport — DECIDED: SSE

**Server-Sent Events** for logs, task transitions, and approval
notifications — not WebSocket.

Reasons:

1. **Traffic is one-way.** The dashboard *monitors* execution; all
   mutations (create/run/cancel/resolve) are plain REST calls. WebSocket's
   full-duplex adds nothing.
2. **SSE rides the existing auth.** It's a GET with the `X-API-Key`
   header (or query-param fallback — see §5); WebSocket would need a
   separate subprotocol/auth handshake through the same capability gates.
3. **Reconnection is free.** `EventSource` auto-reconnects with
   `Last-Event-ID`; the backend replays from the audit log (which is
   already the event store). No connection-state bookkeeping server-side.
4. **Proxies love it.** Plain HTTP, no Upgrade dance — survives the
   reverse-proxy-with-TLS setup from SETUP.md §2.
5. **Backpressure is natural.** The server controls send rate; a slow
   client just lags, which is fine for a log tail.

Implementation sketch: the kernel's audit hook already emits every
transition; an SSE endpoint subscribes a per-request queue to it (plus a
replay of `audit.read(goal_id, limit=N)` on connect). Two streams —
`/goals/{id}/events` (task/approval transitions) and
`/goals/{id}/logs/stream` (raw audit tail) — or one multiplexed stream
(OPEN decision, §8).

## 5. Auth story — DECIDED (with one OPEN point)

- **Reuse the API-key → role system.** No new identity, no sessions, no
  cookies for auth state: the browser stores the key in `localStorage`
  (operator's own machine, loopback) and sends it as `X-API-Key` on every
  request. Roles and per-endpoint capability gates apply unchanged —
  a VIEWER key gets a read-only dashboard for free.
- **SSE auth:** `EventSource` can't set headers → accept the key as a
  one-time `?api_key=` query param on the SSE endpoints *only*, validated
  against the same `verify_api_key` logic (keys in query strings can land
  in proxy logs — document it; loopback-only mitigates). OPEN: vs.
  short-lived SSE tickets minted via `POST /events/ticket` (§8).
- **Non-loopback deployments** keep the `validate_deployment()` fail-fast
  and require TLS in front (unchanged from Milestone 2).

## 6. Security considerations

1. **No new trust boundaries.** The dashboard is a view over the same
   API; every endpoint keeps its capability gate. The artifact-content
   endpoint must jail reads to the workspace and cap size (mirror
   `Workspace.read` + a byte limit).
2. **XSS discipline.** Log/task output contains untrusted content
   (agent-produced text, tracebacks). Render as text, never `innerHTML`;
   React escapes by default — keep it that way (no
   `dangerouslySetInnerHTML` anywhere).
3. **CSRF.** Key-in-header (not cookie) means no ambient credentials —
   CSRF is a non-issue by construction. Do **not** add cookie auth later
   without revisiting this.
4. **Approval actions need ADMIN.** `POST /approvals/{id}/resolve`
   stays `APPROVE_PUBLISH`-gated; the UI must surface 403s honestly
   ("your key lacks APPROVE_PUBLISH") rather than hiding the button's
   failure mode.
5. **Static serving hygiene.** Serve `web/dist` with `html=False` on the
   API routes, correct MIME types, and no directory listing; the SPA
   fallback (`/dashboard/*` → `index.html`) must not shadow `/docs`,
   `/openapi.json`, or API routes.

## 7. Milestone breakdown (3–4 sub-milestones)

- **3.1 Read-only dashboard** — goals list + detail, plan view, task
  outcomes, artifact list, log viewer (polling). Backend: task-status
  projection endpoint. Exit: full visibility, zero new mutation paths.
- **3.2 Run controls + approval queue** — create goal, run, cancel,
  approve/reject from the UI; pending-approvals view. Exit: the milestone
  demo's interactive loop works from the browser.
- **3.3 DAG visualization + artifact browser** — interactive task-DAG
  graph, file tree + capped content viewer. Backend: artifact tree
  endpoint. Exit: a goal's execution is legible end-to-end.
- **3.4 Real-time + hardening** — SSE streams replace polling; auth
  story finalized (query-param key vs tickets); 403/401 UX; static-serve
  wiring; docs (SETUP.md update). Exit: `docker compose up` serves API +
  dashboard on one port.

## 8. DECIDED vs OPEN

**DECIDED**
- Stack: React+Vite+Tailwind static build served by FastAPI (no second server).
- Transport: SSE (not WebSocket) for logs/monitoring.
- Auth: reuse API keys + capability gates; no new identity system; no cookie auth.
- Persistence: no new database — JSONL + workspace files remain the store.
- Scope: single-operator console, not multi-tenant SaaS.

**OPEN**
- One multiplexed `/events` SSE stream vs separate events/logs streams.
- SSE auth: `?api_key=` query param vs short-lived tickets from `POST /events/ticket`.
- DAG rendering library (react-flow vs lighter custom SVG) — evaluate in 3.3.
- Whether `web/dist` is committed or build-only (recommend: build-only, git-ignored, built in release).
- Chat view: thin wrapper over goal creation (3.2) vs a real conversational
  loop — deferred until the LLM provider story matures (rule-based agents
  can't carry a real chat today; don't fake it).
