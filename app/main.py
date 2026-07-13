from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException

from app.config import get_settings
from app.graph import graph
from app.models import DocumentInput, QueryRequest, QueryResponse
from app.security import verify_api_key
from app.vector_store import FaissStore


@asynccontextmanager
async def lifespan(_: FastAPI):
    get_settings()
    yield


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="A LangGraph-powered multi-agent RAG platform.",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.post("/documents", dependencies=[Depends(verify_api_key)])
def add_document(payload: DocumentInput) -> dict[str, int | str]:
    try:
        chunks = FaissStore().add_document(
            document_id=payload.document_id,
            text=payload.text,
            source=payload.source,
        )
        return {"document_id": payload.document_id, "chunks_indexed": chunks}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/query", response_model=QueryResponse, dependencies=[Depends(verify_api_key)])
def query(payload: QueryRequest) -> QueryResponse:
    thread_id = payload.thread_id or str(uuid4())
    try:
        result = graph.invoke(
            {"query": payload.query},
            config={"configurable": {"thread_id": thread_id}},
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    citations = result.get("retrieved_context", [])
    return QueryResponse(
        answer=result.get("answer", ""),
        plan=result.get("plan", []),
        citations=citations,
        review=result.get("review", ""),
        metadata={"thread_id": thread_id, "agents": 5},
    )
