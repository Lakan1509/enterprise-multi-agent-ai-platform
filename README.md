# Enterprise Multi-Agent AI Platform

A GitHub-ready portfolio project demonstrating **enterprise AI agents, RAG,
information retrieval, distributed-system concepts, API engineering, security,
observability-ready architecture, and production deployment practices**.

## Architecture

```text
Client
  |
FastAPI + API-key authentication
  |
LangGraph workflow
  |-- Planner Agent
  |-- Retriever Agent --> OpenAI Embeddings --> FAISS
  |-- Research Agent
  |-- Writer Agent
  |-- Reviewer Agent
  |
Grounded answer + citations + review metadata
```

LangGraph models the workflow as stateful nodes. FAISS performs vector similarity
search. FastAPI provides typed REST endpoints and automatic OpenAPI documentation.

## Features

- Five-stage multi-agent workflow
- Retrieval-Augmented Generation over private documents
- Inline document/chunk citations
- Reviewer agent for grounding and quality checks
- FAISS persistence on local disk
- Thread-scoped LangGraph checkpointing
- API-key protection
- Docker and Docker Compose
- Pytest tests and GitHub Actions CI
- Typed request/response models

## Quick start

### 1. Create the environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Add your OpenAI API key to `.env`.

### 2. Run

```bash
fastapi dev app/main.py
```

Open the generated API documentation at `http://127.0.0.1:8000/docs`.

### 3. Index a document

```bash
curl -X POST http://127.0.0.1:8000/documents \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-this-local-api-key" \
  -d '{
    "document_id": "policy-001",
    "source": "company-policy",
    "text": "Production deployments require peer review, automated tests, rollback plans, encryption, and least-privilege access."
  }'
```

### 4. Ask a question

```bash
curl -X POST http://127.0.0.1:8000/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change-this-local-api-key" \
  -d '{
    "query": "What controls are required before production deployment?",
    "thread_id": "demo-user-1"
  }'
```

## Docker

```bash
cp .env.example .env
docker compose up --build
```

## Tests

```bash
pytest -q
```

## Recommended next upgrades

1. Replace in-memory checkpointing with PostgreSQL.
2. Add Redis caching and rate limiting.
3. Add Prometheus metrics and OpenTelemetry tracing.
4. Deploy to Google Cloud Run or GKE.
5. Add evaluation datasets for retrieval precision and answer groundedness.
6. Add OAuth/OIDC and role-based authorization.
7. Introduce asynchronous task execution for long-running workflows.

## Resume-ready project entry

**Enterprise Multi-Agent AI Platform — Python, LangGraph, FastAPI, FAISS, Docker**

- Designed a stateful five-agent workflow for planning, retrieval, research,
  response generation, and quality review using LangGraph.
- Built a Retrieval-Augmented Generation pipeline with OpenAI embeddings and
  FAISS similarity search, returning document-level citations with generated answers.
- Developed secure, typed REST APIs with FastAPI, API-key authentication,
  containerized deployment, automated testing, and GitHub Actions CI.
- Implemented checkpointing and modular service boundaries to support reliable,
  extensible enterprise AI workflows.

> Only place these bullets on your resume after you run the project and can
> explain its architecture, tradeoffs, and limitations.
