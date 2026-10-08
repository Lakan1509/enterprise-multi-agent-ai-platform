# CASI — Known Limitations (Milestone 2)

Brutally honest accounting of what CASI 0.2.0 is and is not. Labels:
`COMPLETED` = verified working on this VM · `MOCK-SIMPLIFIED` = works but
is a placeholder for the real thing · `EXPERIMENTAL` = partially verified ·
`FUTURE` = not implemented · `BLOCKED` = cannot proceed yet.

## Intelligence — mostly MOCK-SIMPLIFIED

- **The default model layer is a deterministic mock.** (MOCK-SIMPLIFIED)
  `MockProvider` does not reason; agents run their own rule-based
  heuristics. Do not describe CASI as LLM-powered unless a real provider
  is configured and verified.
- **All 7 agents are rule-based.** (MOCK-SIMPLIFIED) Supervisor, coder,
  tester, debugger, reviewer, researcher, planner_agent use
  templates/heuristics — no real reasoning. They are genuinely exercised
  end-to-end, but they will not handle novel problems the way an LLM
  would.
- **The planner is rule-based.** (MOCK-SIMPLIFIED) `decompose_goal`
  handles software goals via pattern matching plus a generic fallback.
  Novel goal types get a shallow generic plan. Blank goals → `ValueError`;
  vague goals → clarifying plan.
- **The debugger's repair strategies are narrow.** (MOCK-SIMPLIFIED)
  Exactly 4 strategies (in-place-sort, missing-import, off-by-one ×2
  variants, wrong-return-type), each proven end-to-end. Anything else
  fails honestly after retries.
- **The demo's first-pass bug is deterministic.** (MOCK-SIMPLIFIED) The
  coder's initial `sort_list` contains the classic
  `list.sort()`-returns-`None` bug by design, so the repair loop has
  something real to fix. The *detection → diagnosis → patch → re-verify*
  cycle is genuine (real pytest in the namespace jail, real traceback,
  real source edit), but the bug's existence is seeded, not discovered.
  This is documented fault-injection, not evidence of general
  self-correction.
- **Ollama provider: EXPERIMENTAL.** Hardened client (timeouts, retries,
  env config) and a REAL smoke test on this VM (Ollama 0.40.1,
  `qwen2.5:0.5b`, `evidence/llm_smoke.log`) — but no agent currently
  routes through it by default, and `ollama pull` doesn't work in
  restricted-DNS sandboxes (use `ollama create` from a GGUF).
- **Nebius provider: EXPERIMENTAL.** 401-auth-error mapping verified live
  with a dummy key; **real completion unverified** — no API key was
  available on this VM.
- **Cost tracking uses estimated tokens** (chars/4) for the mock
  provider. Real provider costs need real token counts.
- **Memory recall is TF-IDF, not embeddings.** (MOCK-SIMPLIFIED)
  Deterministic and persistent (cross-process JSONL tested), but not
  semantic search in the modern sense.
- **Evaluation is thin.** The base repo's eval harness was not ported;
  CASI verifies via test execution + reviewer checks + human approval,
  not benchmark suites.

## Execution & isolation — COMPLETED with acknowledged boundaries

- **The sandbox is containment, not a security boundary.** (COMPLETED)
  The namespace jail is real and tested (31 tests), but it shares the
  host kernel: a kernel privesc defeats it. No seccomp-bpf filter (FUTURE).
  Side channels unmitigated. Host `/usr` is trusted. See SECURITY.md §8.
- **Fork-based sandbox requires main-thread calls.** Threads calling it
  get `SandboxUnavailable`, not a degraded path.
- **btrfs + unprivileged userns quirk.** (verified 2026-10-08) On btrfs,
  the venv is not exposed inside the jail (pre-flight probe skips it);
  the sandbox itself still works, falling back to the system python.
- **umask trap:** jailed processes run as uid 65534 — files installed as
  root without world-readable bits are invisible in the jail
  (`chmod -R a+rX` after such installs; see SETUP.md §1).
- **Checkpoints resume at level granularity**, not mid-task. A crash
  mid-task re-runs that task.
- **Approvals are in-memory only.** (MOCK-SIMPLIFIED) A process restart
  loses the pending approval queue.

## Platform — SIMPLIFIED in the ways that matter

- **Single-tenant API keys.** (MOCK-SIMPLIFIED) No per-user identity, no
  rotation, no TLS in-app. Fine for a local operator console; not a
  multi-user service.
- **API-first; no web UI yet.** (FUTURE — Phase 3, see PHASE3_PLAN.md)
- **Few real integrations.** The plugin registry works; `RagQueryTool`
  (base-repo RAG client) is COMPLETED but disabled by default;
  multimodal and robotics plugins are explicit `NotImplementedError`
  stubs (Phases 7–8).
- **Audit log is append-only by convention.** (MOCK-SIMPLIFIED) No
  tamper-evidence, no signatures, no rotation, no remote shipping.
- **ApprovalGate.wait is thread-based.** Works for one operator; not a
  durable workflow engine.

## What we did NOT do

- No credentials, keys, or secrets in the repo (env vars only; see
  `.env.example`).
- No benchmark numbers, fake integrations, or production-readiness claims
  in code or docs.
- The base repo (`source/`) was not modified; CASI reuses its *patterns*
  (tracing, registry shape, review-retry), not its LangGraph pipeline or
  Ollama dependency. `casi/integrations/plugins.py` imports stdlib only —
  no import-time coupling with the base repo.
- We do not claim the namespace sandbox resists kernel exploits, side
  channels, or a malicious host. It contains untrusted *code*; that is all.
