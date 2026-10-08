# CASI — System Architecture (Milestone 1: Phases 1–2)

**Version:** 0.1.0 · **Date:** 2026-10-08
**Status:** Build in progress. This document is the integration contract — all builders implement against it.

## 1. Design principles

1. **Zero external services to boot.** The core loop (plan → execute → test → repair → verify → approve) runs with nothing but Python 3.11+ and pip packages. No Ollama, no Docker, no network required. (This is a deliberate break from the base repo, whose hard Ollama dependency is documented in the gap analysis.)
2. **Real execution, honest intelligence.** Sandboxing, DAG scheduling, test execution, traceback parsing, patching, approval gates, and audit logging are genuinely implemented. The *language intelligence* is a deterministic `MockProvider` behind a clean `LLMProvider` seam — swappable for a real model later. We never claim the mock reasons.
3. **Fail closed.** Permissions deny by default; approval gates block; sandbox jails the working directory, scrubs the environment, and enforces timeouts.
4. **Everything is observable.** Every goal, task, tool call, test run, patch, approval, and cost event lands in the audit log.

## 2. Package layout

```
~/workspace/casi-ai-os/
  casi/                      # the deliverable package
    __init__.py              # __version__ = "0.1.0"
    config.py                # Settings (pydantic-settings, CASI_* env vars)
    kernel.py                # AIKernel — owns all components, goal lifecycle
    planner.py               # goal decomposition -> Plan[TaskSpec]
    scheduler.py             # DAGScheduler — validation, levels, parallel run, retries, checkpoints
    agents/
      base.py                # Agent ABC, AgentContext, AgentResult, AgentRegistry
      supervisor.py          # capability-based task assignment
      coder.py               # writes code artifacts (via model provider)
      tester.py              # runs pytest in sandbox -> TestReport
      debugger.py            # traceback parsing + repair strategies
      reviewer.py            # artifact review -> approve/reject
      researcher.py          # context gathering from memory + workspace files
    memory/
      store.py               # WorkingMemory + LongTermMemory (TF-IDF recall, JSONL persistence)
    execution/
      sandbox.py             # run(): Docker if available, else hardened subprocess
    models/
      providers.py           # LLMProvider ABC, MockProvider, OllamaProvider (optional), ModelRouter, CostTracker
    security/
      auth.py                # API-key auth (enforced when CASI_API_KEY is set)
      permissions.py         # Capability enum, Role, check()
      approvals.py           # ApprovalGate: request/resolve/pending
      audit.py               # AuditLog -> JSONL
    filesystem/
      workspace.py           # Workspace: files + metadata + version history + search
    integrations/
      plugins.py             # Plugin ABC, PluginRegistry, Multimodal/Robotics stubs
    api/
      app.py                 # FastAPI factory create_app(kernel)
      schemas.py             # request/response models
  tests/                     # pytest suite (one module per component)
  scripts/
    run_milestone_demo.py    # end-to-end acceptance demo
  requirements.txt  Dockerfile  docker-compose.yml  .env.example
  ARCHITECTURE.md  ROADMAP.md  SETUP.md  LIMITATIONS.md  ARCHITECTURE_GAP_ANALYSIS.md
```

## 3. Integration contracts (normative)

### 3.1 Planner (`casi/planner.py`)

```python
@dataclass
class TaskSpec:
    id: str
    name: str
    description: str
    agent_capability: str      # "code" | "test" | "debug" | "review" | "research" | "plan"
    depends_on: list[str]      # task ids; must form a DAG
    max_retries: int = 2
    timeout_s: int = 120
    approval_required: bool = False
    params: dict = field(default_factory=dict)   # e.g. {"file": "sort_list.py", "goal_hint": "..."}

@dataclass
class Plan:
    goal_id: str
    goal_text: str
    tasks: list[TaskSpec]

def decompose_goal(goal_text: str, goal_id: str, model_router=None) -> Plan: ...
```

Rule-based decomposer for software goals (deterministic; real logic, keyword/pattern driven). `model_router` hook reserved for LLM-assisted decomposition later.

### 3.2 Agents (`casi/agents/base.py`)

```python
@dataclass
class AgentResult:
    success: bool
    output: dict[str, Any]
    artifacts: list[str]        # workspace-relative paths produced
    message: str
    error: str | None = None

@dataclass
class AgentContext:
    goal_id: str
    task: TaskSpec
    workspace: Workspace        # casi.filesystem.workspace
    memory: MemorySystem        # working + longterm
    models: ModelRouter         # casi.models.providers
    sandbox: Sandbox            # casi.execution.sandbox
    audit: AuditLog
    approvals: ApprovalGate

class Agent(abc.ABC):
    name: str
    capabilities: tuple[str, ...]
    @abc.abstractmethod
    def run(self, ctx: AgentContext) -> AgentResult: ...

class AgentRegistry:
    def register(self, agent: Agent) -> None
    def find(self, capability: str) -> Agent   # raises NoCapableAgent
    def list(self) -> list[Agent]
```

`SupervisorAgent.assign(task, registry) -> Agent` — capability match, raises on miss.

### 3.3 Scheduler (`casi/scheduler.py`)

```python
@dataclass
class TaskOutcome:
    task_id: str
    status: str                 # "ok" | "failed" | "skipped" | "cancelled"
    attempts: int
    result: AgentResult | None
    error: str | None

@dataclass
class RunReport:
    goal_id: str
    outcomes: list[TaskOutcome]
    success: bool
    started_at: str; finished_at: str

class DAGScheduler:
    def __init__(self, registry, audit, max_workers=4)
    def validate(self, plan: Plan) -> None          # unknown deps, duplicates, cycles -> PlanError
    def levels(self, plan: Plan) -> list[list[TaskSpec]]  # topological levels (Kahn)
    def run(self, plan: Plan, ctx_factory, cancel_event=None) -> RunReport
```

Semantics: levels run sequentially; tasks within a level run in parallel (ThreadPoolExecutor). A task runs only if all deps are `ok`, else `skipped`. Retries with exponential backoff up to `max_retries`. Checkpoints: after each level, run state is persisted to `<data_dir>/runs/<goal_id>/checkpoint.json`; `run()` resumes from checkpoint when present. Cancellation via `threading.Event`.

### 3.4 Execution sandbox (`casi/execution/sandbox.py`)

```python
@dataclass
class ExecResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float

class Sandbox:
    def __init__(self, work_root: Path)
    def run(self, cmd: list[str], cwd: str|Path, timeout_s: int = 60,
            env: dict|None = None) -> ExecResult: ...
    @property
    def backend(self) -> str   # "docker" | "subprocess"
```

Subprocess backend: `cwd` must be inside `work_root` (jail — escape raises `SecurityError`); environment is scrubbed to a minimal allowlist (`PATH`, `PYTHONPATH` for venv, `HOME=/nonexistent`, `LANG`); `resource` limits (CPU seconds, address space) where available; always a timeout; stdout/stderr capped (e.g. 256 KiB each). Docker backend only if a `docker` binary responds — detection must not fail the import.

### 3.5 Memory (`casi/memory/store.py`)

```python
class WorkingMemory:                       # per-goal short term
    def set(self, goal_id, key, value); def get(self, goal_id, key, default=None)
    def get_all(self, goal_id) -> dict; def clear(self, goal_id) -> None

class LongTermMemory:                      # persistent
    def __init__(self, path: Path)
    def remember(self, text: str, metadata: dict|None = None) -> str  # returns entry id
    def recall(self, query: str, k: int = 5) -> list[dict]  # TF-IDF cosine, deterministic

class MemorySystem:
    def __init__(self, data_dir: Path)
    @property working -> WorkingMemory
    @property longterm -> LongTermMemory
```

Recall is genuine TF-IDF over the JSONL store — no embeddings, no network.

### 3.6 Models (`casi/models/providers.py`)

```python
@dataclass
class Completion: text: str; provider: str; model: str
    prompt_tokens: int; completion_tokens: int   # char-based estimates for Mock

class LLMProvider(abc.ABC):
    name: str
    @abc.abstractmethod
    def complete(self, system: str, user: str, **kw) -> Completion: ...

class MockProvider(LLMProvider):   # deterministic, template-driven; name="mock"
class OllamaProvider(LLMProvider): # lazy import of `ollama`; raises HelpfulError if missing/unreachable; name="ollama"

class ModelRouter:
    def __init__(self, providers: dict[str, LLMProvider], default: str = "mock",
                 routes: dict[str, str] | None = None)  # routes: task_kind -> provider name
    def complete(self, task_kind: str, system: str, user: str, **kw) -> Completion

class CostTracker:
    def record(self, completion: Completion, cost_per_1k: float = 0.0) -> None
    def total_tokens(self) -> int; def total_cost(self) -> float; def summary(self) -> dict
```

### 3.7 Security (`casi/security/`)

```python
# permissions.py
class Capability(str, Enum): EXECUTE_CODE, READ_WORKSPACE, WRITE_WORKSPACE, NETWORK, APPROVE_PUBLISH, MANAGE_PLUGINS
class Role(str, Enum): ADMIN, OPERATOR, VIEWER
ROLE_CAPABILITIES: dict[Role, set[Capability]]
def check(role: Role, capability: Capability) -> None  # raises PermissionDenied

# approvals.py
@dataclass
class Approval: id, action, details: dict, status: str  # pending|approved|rejected
    created_at: str; resolved_at: str | None; note: str
class ApprovalGate:
    def request(self, action: str, details: dict) -> Approval   # blocks later via wait()
    def resolve(self, approval_id: str, approved: bool, note="") -> Approval
    def get(self, approval_id) -> Approval; def pending(self) -> list[Approval]
    def wait(self, approval_id, timeout_s=3600, poll_s=1.0) -> Approval  # raises on timeout/reject

# audit.py
class AuditLog:
    def __init__(self, path: Path)
    def record(self, event: str, goal_id="", task_id="", actor="", details=None) -> dict
    def read(self, goal_id="", limit=200) -> list[dict]

# auth.py
def verify_api_key(request) -> Role  # FastAPI dependency; enforces CASI_API_KEY when set
```

### 3.8 Workspace (`casi/filesystem/workspace.py`)

```python
class Workspace:
    def __init__(self, root: Path)
    def write(self, relpath: str, content: str, author="") -> FileVersion  # versions kept (last 10)
    def read(self, relpath: str) -> str
    def exists(self, relpath) -> bool
    def list(self, prefix="") -> list[FileMeta]   # name, size, mtime, version
    def history(self, relpath) -> list[FileVersion]
    def search(self, query: str, k=10) -> list[FileMeta]  # keyword-ranked
```

All paths jailed under `root` (escape → `SecurityError`).

### 3.9 Kernel (`casi/kernel.py`)

```python
class GoalStatus(str, Enum): CREATED, PLANNED, RUNNING, AWAITING_APPROVAL, COMPLETED, FAILED, CANCELLED

@dataclass
class Goal: id, text, status, created_at, plan: Plan|None, report: RunReport|None, artifacts: list[str]

class AIKernel:
    def __init__(self, settings: Settings | None = None)
    # components (public): settings, workspace, memory, models (router), cost, sandbox,
    #                      registry, supervisor, scheduler, approvals, audit, permissions role
    def create_goal(self, text: str, requester: str = "api") -> Goal
    def run_goal(self, goal_id: str, auto_approve: bool = False) -> Goal
        # plan -> schedule -> execute -> verify -> approval gate (unless auto_approve) -> done
    def get_goal(self, goal_id) -> Goal        # raises GoalNotFound
    def list_goals(self) -> list[Goal]
    def cancel_goal(self, goal_id) -> Goal
    def resolve_approval(self, approval_id: str, approved: bool, note="") -> Approval
```

`run_goal` flow: decompose → validate → levels → scheduler.run with ctx_factory wiring all components → collect artifacts → reviewer verdict → if plan has `approval_required` tasks (publish/deploy) → `approvals.request("publish", ...)` → status `AWAITING_APPROVAL` → `wait()` → approved → `COMPLETED`, rejected/timeout → `FAILED` (never auto-publishes).

### 3.10 API (`casi/api/`)

`create_app(kernel: AIKernel) -> FastAPI` with routes:
`GET /health` · `POST /goals` · `GET /goals` · `GET /goals/{id}` · `POST /goals/{id}/run`
`GET /goals/{id}/plan` · `POST /goals/{id}/cancel` · `GET /agents` · `GET /artifacts?goal_id=`
`GET /approvals/pending` · `POST /approvals/{id}/resolve` · `GET /logs?goal_id=&limit=`

### 3.11 Config (`casi/config.py`)

Env vars (all optional, `CASI_` prefix): `CASI_API_KEY`, `CASI_DATA_DIR` (default `./.casi-data`), `CASI_WORKSPACE_DIR` (default `<data>/workspace`), `CASI_MAX_WORKERS` (4), `CASI_DEFAULT_PROVIDER` (`mock`), `CASI_OLLAMA_HOST`, `CASI_REQUEST_TIMEOUT_S`.

## 4. Milestone acceptance flow (concrete)

Goal: *"Write a Python function `sort_list` that sorts a list of numbers ascending, and include unit tests."*

```
create_goal → decompose (7 tasks, DAG):
  t1 write_impl      [coder]                (no deps)
  t2 write_tests     [coder]                (dep t1)
  t3 run_tests       [tester]               (dep t2)   -> FAILS for real (see §5)
  t4 diagnose_repair [debugger]             (dep t3, only if t3 failed)
  t5 rerun_tests     [tester]               (dep t4)
  t6 review          [reviewer]             (dep t5)
  t7 publish_request [approval gate]        (dep t6, approval_required=True)
→ artifacts: workspace/<goal>/sort_list.py (+ versions), test_sort_list.py, TEST_REPORT.json
→ approval "publish" pends; kernel waits; human approves via API/CLI → COMPLETED
```

## 5. The repair loop (how it is real)

The `MockProvider`'s code generator emits a first-pass implementation containing a genuine, classic bug (`return lst.sort()` → returns `None`). The tester executes `pytest` **in the sandbox** against the real files. The debugger parses the **real traceback**, matches it against repair strategies (strategy set is rule-based and documented in `debugger.py`), applies a **real source patch** via the workspace (versioned), and the tester re-runs. If repair fails after `max_retries`, the goal fails honestly — nothing is hardcoded to pass. The bug-in-first-pass is deterministic codegen behavior, documented in `LIMITATIONS.md`; the detection→diagnosis→patch→re-verify loop is genuine.

## 6. Non-goals for this milestone

Full web UI (API-first instead), real LLM reasoning, Docker-in-CI, voice/multimodal, robotics beyond interface stubs, multi-user auth (single API key + roles), network-capable agents (NETWORK capability denied by default).
