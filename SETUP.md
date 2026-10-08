# CASI — Setup Guide

## Requirements
- Python 3.11+
- pip
- (Optional) Docker — only needed if you want the Docker sandbox backend; the default subprocess backend works without it.

## 1. Create a virtual environment

```bash
cd ~/workspace/casi-ai-os
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Configure (optional)

```bash
cp .env.example .env
# Edit .env — set CASI_API_KEY to require API-key auth, CASI_DATA_DIR for storage.
```

All settings are optional; sensible defaults work out of the box. See `.env.example` for the full list.

## 3. Run the milestone demo (acceptance test)

```bash
python scripts/run_milestone_demo.py
```

This runs the full loop: goal → plan → agents → sandboxed execution → test failure → repair → verified artifact → **approval gate pauses** before publish. Approve with `y` when prompted (or resolve via the API).

## 4. Run the API server

```bash
uvicorn casi.api.app:create_app --factory --host 0.0.0.0 --port 8000
```

Then: `curl localhost:8000/health`. Interactive docs at `http://localhost:8000/docs`.
Pass `X-API-Key` header if `CASI_API_KEY` is set.

## 5. Run the tests

```bash
pytest -q
```

## 6. Security scan

```bash
bandit -r casi/ -f txt
```

## 7. Docker (optional)

```bash
docker compose up --build
```

Serves the API on port 8000 with `./.casi-data` persisted.
