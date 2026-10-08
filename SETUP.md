# CASI — Setup Guide

## Requirements

- **Linux** (the namespace sandbox is Linux-only; it uses `unshare(2)`/`chroot(2)`).
  Kernel with unprivileged user namespaces enabled (`/usr/bin/unshare` must exist).
- Python 3.11+
- pip
- No Docker needed. The Docker backend was removed in Milestone 2; the only
  sandbox mode is `"strict"` (namespace jail).

## 1. Create a virtual environment

```bash
cd ~/workspace/casi-ai-os
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Important — umask/world-readability:** jailed commands run as uid 65534
(`nobody`), *not* as your user. Any host file the jail must read
(site-packages, test data) needs world-readable bits. If you `pip install`
as root outside a venv, run afterwards:

```bash
chmod -R a+rX /path/to/site-packages
```

(Inside a normal user-owned venv this is usually already fine.)

## 2. Configure

```bash
cp .env.example .env
# Edit .env — see the table below.
```

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `CASI_API_KEY` | *(empty)* | Admin API key (`X-API-Key` header). Empty = auth disabled (loopback dev only). |
| `CASI_OPERATOR_API_KEY` | *(empty)* | Optional key authenticating as OPERATOR. |
| `CASI_VIEWER_API_KEY` | *(empty)* | Optional key authenticating as VIEWER (read-only). |
| `CASI_REQUIRE_AUTH` | `0` | `1` = require auth even on loopback; startup fails without a strong key. |
| `CASI_HOST` | `127.0.0.1` | API bind address. Non-loopback requires a strong key (fail-fast). |
| `CASI_PORT` | `8000` | API port. |
| `CASI_DATA_DIR` | `./.casi-data` | Goals, runs, memory, audit logs, workspace. |
| `CASI_WORKSPACE_DIR` | `<data>/workspace` | Agent artifact root. |
| `CASI_MAX_WORKERS` | `4` | Parallel task workers in the DAG scheduler. |
| `CASI_DEFAULT_PROVIDER` | `mock` | `mock` \| `ollama` \| `nebius`. |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama server URL. |
| `OLLAMA_MODEL` | `qwen2.5:0.5b` | Default Ollama model. |
| `CASI_REQUEST_TIMEOUT_S` | `60` | Default timeout for sandboxed commands. |
| `CASI_SANDBOX_MODE` | `strict` | Only value accepted; anything else → `ValueError`. |
| `CASI_SANDBOX_VENV` | *(auto)* | Path to a venv to expose read-only at `/venv` in the jail; empty string disables. Auto-detects the current venv otherwise. |
| `NEBIUS_API_KEY` | *(empty)* | Nebius API key — read from env **at call time**, never cached. |
| `NEBIUS_MODEL` | `meta-llama/Meta-Llama-3.1-8B-Instruct` | Default Nebius model. |
| `CASI_RAG_API_URL` | *(empty)* | Base URL of the base-repo RAG app. Unset = `RagQueryTool` disabled. |
| `CASI_RAG_API_KEY` | *(empty)* | API key for the base-repo RAG app's `POST /query`. |

Generate a strong key (≥ 32 chars):

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Deployment matrix

| Deployment | Result |
|---|---|
| `CASI_HOST=127.0.0.1`, no key | Starts with a loud warning. Dev only, loopback-only. |
| `CASI_HOST=127.0.0.1` + strong key | Recommended local setup. |
| `CASI_HOST=0.0.0.0` (or any non-loopback) + strong key | Minimum for network exposure. Put TLS in front (reverse proxy). |
| Non-loopback **without** strong key | **Refused at startup** (`InsecureDeploymentError`). |
| `CASI_REQUIRE_AUTH=1` without strong key | **Refused at startup**, even on loopback. |

`0.0.0.0` is deliberately *not* treated as loopback. A strong key is
≥ 32 chars and not a placeholder (`changeme`, `password`, `test`,
single-repeated-char, … are rejected).

## 3. Sandbox prerequisites

- Linux with unprivileged user namespaces: check with
  `unshare --map-root-user true` (should succeed).
- The sandbox forks, so it must be called from the **main thread**.
- **btrfs caveat (verified on this VM):** btrfs denies unprivileged
  user-namespace processes access to files regardless of permission bits.
  CASI pre-flights the venv for readability by uid 65534 and skips venv
  exposure with a `RuntimeWarning` on such filesystems — the sandbox
  itself still runs fully namespaced; the jail falls back to the system
  python. Workaround: put the venv on ext4/overlayfs/tmpfs, or set
  `CASI_SANDBOX_VENV` to a venv on a compatible filesystem, or install
  needed packages (e.g. `pytest`) into the system python (world-readable,
  see §1).

## 4. Ollama setup (optional, EXPERIMENTAL provider)

The install script needs `zstd`:

```bash
# 1. Install Ollama (needs zstd available)
curl -fsSL https://ollama.com/install.sh | sh

# 2. Serve
ollama serve &

# 3. Get a model — RAM floor ~1 GB for qwen2.5:0.5b; ~5 GB for :7b
ollama pull qwen2.5:0.5b
```

If `ollama pull` fails (restricted-DNS sandboxes block it), import from a
Hugging Face GGUF instead:

```bash
# download the .gguf, then:
ollama create qwen2.5:0.5b -f ./Modelfile   # Modelfile: FROM ./qwen2.5-0.5b-instruct.gguf
```

Then:

```bash
CASI_DEFAULT_PROVIDER=ollama OLLAMA_MODEL=qwen2.5:0.5b python scripts/run_llm_smoke.py
```

Expected: `list_models` → your model, a real completion (`SMOKE-OK`),
token/cost accounting. A reference transcript lives at
`evidence/llm_smoke.log` (Ollama 0.40.1, verified 2026-10-08).

## 5. Nebius setup (optional, EXPERIMENTAL provider)

```bash
export NEBIUS_API_KEY="<redacted>"   # never commit; env only
# optional: export NEBIUS_MODEL=meta-llama/Meta-Llama-3.1-8B-Instruct
CASI_DEFAULT_PROVIDER=nebius python scripts/run_llm_smoke.py
```

Note: on this VM the completion path is **unverified live** (no key was
available); only the 401-auth-error mapping was verified with a dummy key.

## 6. Run the milestone demo (acceptance test)

```bash
python scripts/run_milestone_demo.py
```

Full loop: goal → plan → agents → **namespace-sandboxed** execution →
test failure → repair → verified artifact → **approval gate pauses**
before publish. Approve with `y` when prompted (or resolve via the API),
or run non-interactively with the explicit admin path:

```bash
python scripts/run_milestone_demo.py --auto-approve
```

`--auto-approve` passes `role=Role.ADMIN` through the same authorized
`APPROVE_OWN_RUNS` check as any API caller — it is not a backdoor.

## 7. Run the API server

```bash
uvicorn casi.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

(`--factory` because `create_app` takes the kernel.) Then:

```bash
curl localhost:8000/health
# with auth: curl -H "X-API-Key: <redacted>" localhost:8000/health
```

Interactive docs at `http://localhost:8000/docs`. Bind `0.0.0.0` only with
a strong `CASI_API_KEY` (see the deployment matrix) and TLS in front.

## 8. Run the tests

```bash
pytest -q            # from the repo root collects tests/ (285 tests)
pytest tests/test_sandbox.py -q   # 31 namespace-sandbox tests
```

Note: running bare `pytest` from the root also collects the read-only
`source/` base-repo tests, which need that repo's own dependencies —
scope to `tests/` for the CASI suite. Last full run: **285 passed,
0 failed** (2026-10-08).

## 9. Security scan

```bash
bandit -r casi/ -f txt
```

## 10. Docker (optional)

`Dockerfile` / `docker-compose.yml` package the API server only — the
sandbox is the namespace jail, not Docker-in-Docker. The compose file
persists `./.casi-data`.

```bash
docker compose up --build
```

Serves the API on port 8000.
