# Enterprise Multi-Agent AI Platform

A production-style **multi-agent Retrieval-Augmented Generation (RAG) platform** built with Python, FastAPI, LangGraph, Ollama, Qwen 2.5, EmbeddingGemma, and FAISS.

The project demonstrates enterprise AI engineering patterns including multi-agent orchestration, semantic retrieval, grounded generation, citations, response review, conversational checkpointing, API security, automated testing, and containerized deployment.

---

## Architecture

```text
                         Client
                           |
                           v
                    FastAPI REST API
                           |
                           v
                  API-Key Authentication
                           |
                           v
                    LangGraph Workflow
                           |
                           v
                     Planner Agent
                           |
                           v
                    Retriever Agent
                           |
                           v
             Ollama Embeddings (EmbeddingGemma)
                           |
                           v
                         FAISS
                           |
                           v
                    Research Agent
                           |
                           v
                     Writer Agent
                           |
                           v
                    Reviewer Agent
                           |
                           v
                       Finalizer
                           |
                           v
              Grounded Answer + Citations
```

LangGraph manages the application as a stateful multi-agent workflow.

Documents are converted into embeddings locally using **EmbeddingGemma through Ollama** and stored in a **FAISS vector index**.

User queries are embedded using the same embedding model and matched against indexed document chunks using vector similarity search.

Retrieved context is then processed through specialized agents before the final grounded response is returned.

---

## Multi-Agent Workflow

The system implements a multi-stage LangGraph workflow.

### 1. Planner Agent

Analyzes the incoming request and prepares the execution strategy for the downstream agents.

### 2. Retriever Agent

Searches the FAISS vector index for document chunks semantically related to the user's query.

### 3. Research Agent

Analyzes the retrieved context and extracts information relevant to the user's request.

The research stage is designed to remain grounded in retrieved documents.

### 4. Writer Agent

Produces a structured answer using the research output and retrieved context.

### 5. Reviewer Agent

Reviews the generated response for grounding, citation quality, and consistency with the retrieved information.

### 6. Finalizer

Returns the final reviewed answer and associated metadata to the API client.

---

## Retrieval-Augmented Generation

The platform includes a local RAG pipeline:

```text
Document
   |
   v
Text Chunking
   |
   v
EmbeddingGemma
   |
   v
Vector Normalization
   |
   v
FAISS Index
   |
   v
Semantic Search
   |
   v
Retrieved Context
   |
   v
Multi-Agent Processing
   |
   v
Grounded Answer
```

FAISS performs efficient vector similarity search over indexed document chunks.

The vector index and associated metadata can be persisted locally so indexed knowledge can be reused between application runs.

---

## Local LLM Architecture

The project runs its AI models locally through **Ollama**.

### Language Model

```text
qwen2.5:7b
```

Used for multi-agent reasoning and response generation.

### Embedding Model

```text
embeddinggemma
```

Used to create vector representations of documents and queries for semantic retrieval.

This architecture allows the project to demonstrate a complete RAG and agentic AI workflow without requiring a hosted LLM API.

---

## Features

- LangGraph multi-agent orchestration
- Planner agent
- Retriever agent
- Research agent
- Writer agent
- Reviewer agent
- Final response stage
- Retrieval-Augmented Generation (RAG)
- FAISS vector similarity search
- Local Ollama inference
- Qwen 2.5 7B language model
- EmbeddingGemma embeddings
- Document chunking
- Persistent vector index
- Grounded answer generation
- Document/chunk citations
- Response review and validation
- LangGraph checkpointing
- Thread-scoped workflow state
- FastAPI REST APIs
- API-key authentication
- Pydantic request/response models
- Swagger/OpenAPI documentation
- Docker support
- Docker Compose support
- Pytest automated testing
- GitHub Actions CI

---

## Technology Stack

| Category | Technology |
| --- | --- |
| Programming Language | Python |
| API Framework | FastAPI |
| Agent Orchestration | LangGraph |
| Local LLM Runtime | Ollama |
| Language Model | Qwen 2.5 7B |
| Embedding Model | EmbeddingGemma |
| Vector Database / Search | FAISS |
| AI Architecture | Multi-Agent RAG |
| Validation | Pydantic |
| Testing | Pytest |
| Containerization | Docker |
| Local Orchestration | Docker Compose |
| CI | GitHub Actions |

---

## Project Structure

```text
enterprise-multi-agent-ai-platform/
├── .github/
│   └── workflows/
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── graph.py
│   ├── llm.py
│   ├── main.py
│   ├── models.py
│   ├── security.py
│   └── vector_store.py
├── data/
├── tests/
│   ├── test_api.py
│   └── test_chunking.py
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── Makefile
├── README.md
└── requirements.txt
```

---

## Requirements

Before running the project, install:

- Python 3.11+
- Ollama
- Git

Docker is optional if you want to run the application in containers.

---

## Installation

Clone the repository:

```bash
git clone <repository-url>
cd enterprise-multi-agent-ai-platform
```

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on macOS/Linux:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

---

## Ollama Setup

Make sure Ollama is installed and running.

Verify the installation:

```bash
ollama --version
```

Install the language model:

```bash
ollama pull qwen2.5:7b
```

Install the embedding model:

```bash
ollama pull embeddinggemma
```

Verify the models:

```bash
ollama list
```

You should see models similar to:

```text
embeddinggemma:latest
qwen2.5:7b
```

Start Ollama if it is not already running:

```bash
ollama serve
```

By default Ollama listens on:

```text
127.0.0.1:11434
```

---

## Environment Configuration

Copy the example environment configuration:

```bash
cp .env.example .env
```

Configure the values required by the application.

Do not commit `.env` or any real credentials to Git.

The project uses **local Ollama models**, so an OpenAI API key is not required for the current implementation.

---

## Running the Application

Start the FastAPI server:

```bash
uvicorn app.main:app --reload
```

If port `8000` is already being used:

```bash
uvicorn app.main:app --reload --port 8001
```

Open the interactive API documentation at:

```text
http://127.0.0.1:8000/docs
```

or, when using port 8001:

```text
http://127.0.0.1:8001/docs
```

---

## API Endpoints

### Health Check

```text
GET /health
```

Checks whether the FastAPI application is running.

---

### Add Document

```text
POST /documents
```

Adds a document to the RAG knowledge base.

Example request:

```json
{
  "document_id": "deployment-policy-001",
  "source": "Enterprise Deployment Policy",
  "text": "All production AI deployments require automated testing, peer code review, a documented rollback plan, encryption of sensitive data, least-privilege access controls, monitoring, and approval from the engineering lead before release."
}
```

The document is:

1. Received through FastAPI
2. Split into chunks
3. Embedded with EmbeddingGemma
4. Normalized
5. Stored in FAISS
6. Associated with document metadata

---

### Query Knowledge Base

```text
POST /query
```

Runs a user question through the multi-agent RAG workflow.

Example:

```json
{
  "query": "What controls are required before an AI system can be deployed to production?",
  "thread_id": "demo-user-1"
}
```

The request flows through:

```text
Query
  ↓
Planner
  ↓
Retriever
  ↓
FAISS
  ↓
Researcher
  ↓
Writer
  ↓
Reviewer
  ↓
Finalizer
  ↓
Grounded Response + Citations
```

---

## API Security

Protected endpoints support API-key authentication.

The API key is supplied through the:

```text
X-API-Key
```

header.

Example:

```bash
curl -X POST http://127.0.0.1:8000/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-local-api-key" \
  -d '{
    "query": "What controls are required before production deployment?",
    "thread_id": "demo-user-1"
  }'
```

Never commit real API keys to GitHub.

---

## Testing

Run the test suite using the project's Python interpreter:

```bash
python -m pytest -q
```

The tests cover API behavior and document chunking functionality.

---

## Docker

Build the Docker image:

```bash
docker build -t enterprise-multi-agent-ai-platform .
```

Run the container:

```bash
docker run -p 8000:8000 enterprise-multi-agent-ai-platform
```

Docker Compose configuration is also included:

```bash
docker compose up --build
```

---

## Production Engineering Concepts Demonstrated

This project demonstrates several concepts relevant to production AI/ML engineering:

### Agent Orchestration

LangGraph provides explicit state transitions between specialized AI agents.

### Retrieval-Augmented Generation

Relevant information is retrieved before response generation to improve grounding.

### Semantic Search

EmbeddingGemma converts text into vector representations that can be searched efficiently with FAISS.

### Local Model Serving

Ollama provides local inference for both the language model and embedding model.

### Grounding and Review

Generated responses pass through a reviewer stage before being finalized.

### API Engineering

FastAPI provides typed endpoints, validation, dependency injection, and automatic OpenAPI documentation.

### Security

API-key authentication protects application endpoints.

### Stateful Execution

LangGraph checkpointing supports thread-scoped workflow state.

### Testing

Pytest validates important application behavior.

### Containerization

Docker and Docker Compose provide reproducible deployment environments.

---

## Current Limitations

This project is designed as a portfolio and engineering demonstration rather than a complete enterprise SaaS platform.

Current limitations include:

- Local FAISS storage rather than a distributed vector database
- Local Ollama inference rather than horizontally scaled model serving
- Simple API-key authentication rather than enterprise OAuth/OIDC
- In-memory LangGraph checkpointing
- Limited automated evaluation coverage
- No distributed tracing backend
- No production Kubernetes deployment

These are natural extension points for future development.

---

## Future Improvements

Potential next steps include:

1. Replace in-memory checkpointing with PostgreSQL.
2. Add Redis caching and rate limiting.
3. Add Prometheus metrics.
4. Add OpenTelemetry distributed tracing.
5. Add structured LLM evaluation datasets.
6. Measure retrieval precision and recall.
7. Measure answer groundedness and citation accuracy.
8. Add OAuth/OIDC authentication.
9. Add role-based access control.
10. Introduce asynchronous agent execution.
11. Add model fallback and retry policies.
12. Deploy to Kubernetes or a cloud container platform.
13. Add production observability dashboards.
14. Add load and latency benchmarking.

---

## Resume-Ready Project Description

**Enterprise Multi-Agent AI Platform — Python, LangGraph, FastAPI, Ollama, Qwen 2.5, EmbeddingGemma, FAISS, Docker**

Designed and implemented a stateful multi-agent RAG platform using LangGraph with specialized planning, retrieval, research, generation, and review stages. Built semantic document retrieval using EmbeddingGemma embeddings and FAISS vector similarity search, integrated local Qwen 2.5 inference through Ollama, and developed typed FastAPI endpoints with API-key authentication, grounded citations, checkpointing, automated testing, and containerized deployment.

---

## Purpose

This project demonstrates practical skills in:

**Generative AI · Agentic AI · Multi-Agent Systems · RAG · LLM Engineering · LangGraph · Local LLM Inference · Semantic Search · Vector Databases · FastAPI · API Security · Docker · Testing · Production AI Engineering**