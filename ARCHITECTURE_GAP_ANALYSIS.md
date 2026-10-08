# CASI — Architecture Gap Analysis

**Date:** 2026-10-08
**Auditor:** Lux (build coordinator)
**Base repo:** `Lakan1509/enterprise-multi-agent-ai-platform` (cloned read-only to `~/workspace/casi-ai-os/source/`)
**Method:** Read the code. Every verdict below is backed by file:line evidence. Nothing is claimed to work that was not read and verified.

---

## 1. Verdict table: claimed components

| Claimed component | Verdict | Evidence |
|---|---|---|
| `RealAgentRuntime` | ❌ **Does not exist** | Name appears nowhere in the repo (grep over all `.py` files: zero hits). |
| `TaskExecutor` | ❌ **Does not exist** | Zero hits anywhere. |
| `GoalRunner` | ❌ **Does not exist** | Zero hits anywhere. |
| `ReplanningController` | ❌ **Does not exist** | Zero hits. A bounded review/retry loop exists (`app/agents/reviewer.py` `finalize_review`, PASS/REVISE parsing with a retry cap), but that is *review-retry*, not replanning — it never generates a new plan. |
| DAG scheduling | ❌ **Absent** | The orchestration is a LangGraph linear pipeline (planner → retriever → researcher → writer → reviewer → finalizer). No topological sort, no dependency resolution, no parallel branches (grep for `DAG|topological|dependenc` found nothing in `app/`). |
| RAG retrieval | ✅ **Genuinely works** | `app/llm.py:26-36` (`LLMClient.embed` via Ollama `embeddinggemma`), `app/vector_store.py:52-83` (real FAISS `IndexFlatIP` with L2-normalized cosine search, persisted to `data/faiss.index` + `data/metadata.json`). Chunker: 900-char chunks, 120 overlap (`vector_store.py:12-26`). **Caveat:** requires a running local Ollama server; no reranking. |
| FastAPI services | ✅ **Genuinely works** | `app/main.py` — `GET /health`, `POST /documents`, `POST /query` with `QueryRequest`/`QueryResponse` schemas (`app/models.py`). API-key auth exists (`app/security.py`) but is **weak**: if `api_key` is unset (the default), auth is silently disabled. |
| Agent registry | ❌ **Absent** | "Agents" are single-shot LLM-call functions wrapped as LangGraph nodes (verified: `app/graph.py` wiring + all `app/agents/*.py`). No registry, no capability lookup, no agent lifecycle. `app/agents/retriever.py` is dead code — never called by the graph. `__init__.py` files are empty. |
| Tool registry | ⚠️ **Partial** | `app/tools/registry.py` exists but is a name-keyed dict with **one** registered tool (the retrieval tool); only the retrieval node calls a tool. No capability-based lookup. |
| Docker execution / sandboxing | ❌ **Absent in app code** | Docker is used only to *containerize the API itself* (`Dockerfile`, `docker-compose.yml`). Zero `docker`/`subprocess`/`container` references in `app/`. No code execution of any kind exists. |
| Nebius model integration | ❌ **Absent** | Zero hits across code, docs, and configs. The LLM layer is **Ollama-only** (`app/llm.py` uses `ollama.Client`, default model `qwen2.5:7b`, host `http://localhost:11434`). |
| Execution reports & evaluation | ⚠️ **Works but thin** | The evaluation pipeline is genuine: `app/evaluation/{evaluator,runner,metrics,report,cli}.py`, retrieval eval (`retrieval_runner.py`, `retrieval_metrics.py`: hit@k, MRR), tracing (`app/observability/tracing.py`). **But:** golden set is only 3 tasks (`data/golden_tasks.json`), groundedness scoring is heuristic, and `main.py:metadata["agents"] = 7` is a hardcoded literal, not a count. |
| Memory module | ❌ **Empty** | `app/memory/__init__.py` is **0 bytes**. The directory exists; nothing is in it. |
| Planner | ⚠️ **Decorative** | `app/agents/planner.py:8-37` genuinely prompts the LLM for 3–5 steps, but the plan is **never consumed by any downstream node** — it is only echoed in the API response (`main.py: plan=result.get("plan", [])`). Planning has zero effect on execution. |
| Supervisor | ✅ **Genuinely routes** | Keyword router + LLM fallback (verified in `app/graph.py` / `app/agents/supervisor.py`). Real conditional routing. |
| Deterministic fast-answer path | ✅ **Strongest piece** | `retrieval_router.py` → `grounded_answer.py:40-114`: extractive sentence selection with exact citations. Works with **zero LLM**. |
| Checkpointing | ⚠️ **Cosmetic** | `InMemorySaver` is wired in but no node reads prior turns — no real conversational memory. |

## 2. What the repo actually is

A **well-structured LangGraph RAG Q&A pipeline with a review-retry loop and an evaluation harness** — not an agent runtime and not an operating system of any kind. Honest, working code for what it does; the naming in the user's spec (`RealAgentRuntime`, `GoalRunner`, etc.) describes an aspiration, not the codebase.

**Hard dependency:** a running local Ollama server (chat + embedding models). Without it, every path except the deterministic fast-answer one fails and `main.py` surfaces it as HTTP 503. This makes the repo unusable in environments without Ollama — a real portability problem for CASI.

## 3. Gaps vs. the CASI target architecture

| CASI component | Base repo status | Gap |
|---|---|---|
| 1. AI Kernel (goals, scheduling, state, permissions, budgets) | ❌ None | **Build from scratch.** |
| 2. Cognitive Engine (intent, decomposition, self-correction, verification) | ⚠️ Decorative planner only | **Build:** decomposition that drives execution; failure analysis; outcome verification. |
| 3. Multi-Agent Runtime (supervisor, coding, research, testing, debugging, planning, data-analysis agents) | ⚠️ Supervisor routing only; agents are single LLM prompts | **Build:** real agent runtime with registry, capability-based assignment, and *acting* agents (coder, tester, debugger) that execute and verify. |
| 4. Memory System | ❌ Empty module | **Build:** working short-term/long-term memory with retrieval. |
| 5. Execution Engine (DAG, parallel, retries, checkpoints, cancellation, sandbox) | ❌ None | **Build from scratch.** LangGraph gives us nothing reusable here for DAG task scheduling. |
| 6. AI File System (workspace, metadata, semantic search, versions) | ❌ None | **Build from scratch.** |
| 7. Application Integration (APIs, plugins) | ⚠️ One-tool registry | **Build:** plugin interface + registry; stub integrations. |
| 8. AI OS Interface (dashboard APIs: chat/tasks/agents/approvals/logs) | ⚠️ Q&A endpoints only (`/health`, `/documents`, `/query`) | **Extend:** goal/task/approval/artifact/log endpoints. |
| 9. Model Intelligence Layer (provider interface, routing, cost) | ❌ Ollama-only, no abstraction | **Build:** `LLMProvider` ABC, mock provider, router, cost tracker. Optional Ollama adapter for compat. |
| 10. Security (authN/authZ, least privilege, isolation, secrets, audit, approval gates) | ⚠️ API key (weak, off-by-default) | **Build:** real permission model, audit log, approval gates, sandboxed execution. |
| 11–12. Multimodal / robotics | ❌ None | **Stubs/interfaces only** (per spec). |

## 4. What we reuse from the base repo

Deliberately **little code, several ideas**:

- **Reuse (adapted, not copied):** the tracing pattern (`ExecutionTrace` → CASI `AuditLog`/execution events); the evaluation-metrics *approach* (heuristic, honest about being heuristic); the tool-registry *pattern* (but capability-based); the review-retry *pattern* (as one repair strategy among several, inside a real execution loop).
- **Do NOT reuse:** the LangGraph graph itself (wrong abstraction for DAG task scheduling — it is a linear Q&A pipeline); the Ollama hard dependency (CASI must boot and run its core loop with zero external services); the decorative planner.
- **Notable:** the base repo's strongest engineering (deterministic extractive QA, FAISS retrieval) is irrelevant to CASI's milestone, which is software-engineering task execution, not Q&A. We do not port it.

## 5. Risks and honest limitations

1. **No real LLM in this environment.** CASI's cognitive engine will be rule-based/deterministic for this milestone, with a clean `LLMProvider` seam so a real model can be plugged in later. We will not pretend the mock provider is intelligent.
2. **No Docker in this environment.** Sandboxed execution uses a hardened `subprocess` fallback (timeouts, cwd jail, env scrubbing, `resource` limits). Documented in `LIMITATIONS.md`.
3. **The self-correction demo is real but narrow.** The repair loop genuinely executes tests, parses real tracebacks, and applies real patches — but the strategy set is rule-based, not general reasoning. This is stated plainly in the docs, not oversold.
