#!/usr/bin/env python3
"""Real (non-mocked) smoke test for the Ollama provider path.

Requires a running Ollama server with a pulled model (see SETUP.md for the
install steps). Honest by construction: every value printed below comes
from a live HTTP call against the local Ollama REST API. If the server is
unreachable, the model is missing, or a request fails, the script prints
the provider error and exits non-zero — it never fabricates model output.

Flow:
  (a) list models via ``OllamaProvider.list_models()`` (``GET /api/tags``);
  (b) send one real completion through ``OllamaProvider.complete()`` and one
      through the CASI ``ModelRouter`` (``CASI_DEFAULT_PROVIDER=ollama``);
  (c) print the real model output plus token / cost accounting.

Usage:
    cd ~/workspace/casi-ai-os
    .venv/bin/python scripts/run_llm_smoke.py [--host ...] [--model ...] \\
        | tee evidence/llm_smoke.log
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# Allow running from the repo root without installation.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from casi.models import (  # noqa: E402
    CostTracker,
    OllamaProvider,
    ProviderError,
    build_default_router,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("OLLAMA_HOST"),
                        help="Ollama server base URL (default: $OLLAMA_HOST or http://127.0.0.1:11434)")
    parser.add_argument("--model", default=os.environ.get("OLLAMA_MODEL"),
                        help="Model to query (default: $OLLAMA_MODEL or qwen2.5:0.5b)")
    parser.add_argument("--prompt", default="Reply with exactly: SMOKE-OK",
                        help="User prompt to send")
    args = parser.parse_args()

    provider = OllamaProvider(host=args.host, model=args.model)
    tracker = CostTracker()
    started = time.monotonic()
    print(f"[smoke] provider=ollama host={provider.host} model={provider.model}", flush=True)

    try:
        # (a) list models via the provider
        models = provider.list_models()
        print(f"[smoke] GET /api/tags -> {len(models)} model(s): {models}", flush=True)
        if provider.model not in models:
            print(f"[smoke] WARNING: requested model {provider.model!r} not in "
                  f"/api/tags list; the chat call below will likely fail.", flush=True)

        system = "You are a terse assistant. Follow instructions exactly."
        user = args.prompt

        # (b1) real completion through OllamaProvider directly
        t1 = time.monotonic()
        direct = provider.complete(system, user, temperature=0.0)
        tracker.record(direct)  # local inference: cost_per_1k=0.0
        print(f"[smoke] OllamaProvider.complete ({time.monotonic() - t1:.1f}s) ->", flush=True)
        print("----- model output (direct) -----")
        print(direct.text)
        print("---------------------------------")
        print(f"[smoke] tokens: prompt={direct.prompt_tokens} "
              f"completion={direct.completion_tokens}", flush=True)

        # (b2) real completion through the CASI ModelRouter
        os.environ["CASI_DEFAULT_PROVIDER"] = "ollama"
        router = build_default_router()
        print(f"[smoke] ModelRouter available providers: {router.available()}", flush=True)
        t2 = time.monotonic()
        routed = router.complete("smoke-test", system, user, temperature=0.0)
        tracker.record(routed)
        print(f"[smoke] ModelRouter.complete ({time.monotonic() - t2:.1f}s) ->", flush=True)
        print("----- model output (routed) -----")
        print(routed.text)
        print("---------------------------------")
        print(f"[smoke] tokens: prompt={routed.prompt_tokens} "
              f"completion={routed.completion_tokens}", flush=True)

        # (c) token / cost accounting
        summary = tracker.summary()
        print(f"[smoke] CostTracker summary: total_tokens={summary['total_tokens']} "
              f"total_cost=${summary['total_cost']:.6f} "
              f"(local Ollama inference is free; cost_per_1k=0.0) "
              f"by_provider={summary['by_provider']}", flush=True)
        print(f"[smoke] DONE in {time.monotonic() - started:.1f}s — "
              f"all output above came from live Ollama calls.", flush=True)
        return 0
    except ProviderError as exc:
        print(f"[smoke] BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        print("[smoke] No model output was produced; nothing was fabricated.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
