# CASI — Known Limitations (Milestone 1)

Honest accounting of what CASI 0.1.0 cannot do. Nothing here is presented as production-ready.

## Intelligence
- **The model layer is a deterministic mock.** `MockProvider` does not reason; agents use their own rule-based logic. The `LLMProvider` seam exists so a real model can be plugged in later, but out of the box CASI's "cognition" is templates + heuristics. Do not describe CASI as LLM-powered without a real provider configured.
- **The planner is rule-based.** `decompose_goal` handles software goals via pattern matching plus a generic fallback. Novel goal types get a shallow generic plan.
- **The debugger's repair strategies are narrow.** It handles a documented set of failure patterns (e.g. `return x.sort()` → `sorted(x)`, missing stdlib imports). Anything else fails honestly after retries.
- **The demo's first-pass bug is deterministic.** The coder's initial implementation of `sort_list` contains the classic `list.sort()`-returns-`None` bug by design, so the repair loop has something real to fix. The *detection → diagnosis → patch → re-verify* cycle is genuine (real pytest, real traceback, real source edit), but the bug's existence is seeded, not discovered. This is the documented fault-injection for the milestone, not evidence of general self-correction.

## Execution & isolation
- **No Docker in this environment.** The sandbox uses a hardened subprocess backend: working-directory jail, scrubbed environment, `resource` limits (POSIX only), timeouts, output caps. This is *containment*, not a security boundary — a determined local process can still misbehave. Treat accordingly.
- **Single-machine, single-tenant.** No multi-user auth (one API key + coarse roles), no encrypted secrets store (env vars only), no network egress controls beyond the default-deny `NETWORK` capability.

## Platform
- **API-first; no web UI yet.** Phase 3.
- **No real integrations.** The plugin registry works, but multimodal and robotics plugins are explicit `NotImplementedError` stubs (Phases 7–8).
- **Memory recall is TF-IDF**, not embeddings. Good enough for small stores; not semantic search in the modern sense.
- **Evaluation is thin.** The base repo's eval harness was not ported; CASI verifies via test execution + reviewer checks + human approval, not benchmark suites.
- **Checkpoints resume at level granularity**, not mid-task. A crash mid-task re-runs that task.
- **Cost tracking uses estimated tokens** (chars/4) for the mock provider. Real provider costs require real token counts.

## What we did NOT do
- No credentials, keys, or secrets are in the repo (env vars only; see `.env.example`).
- No benchmark numbers, fake integrations, or production-readiness claims appear anywhere in code or docs.
- The base repo (`source/`) was not modified; CASI reuses its *patterns* (tracing, registry shape, review-retry), not its LangGraph pipeline or Ollama dependency.
