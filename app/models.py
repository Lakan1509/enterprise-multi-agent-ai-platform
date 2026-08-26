from typing import Any

from pydantic import BaseModel, Field

from app.evaluation.schemas import EvaluationResult


class DocumentInput(BaseModel):
    document_id: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=20)
    source: str = "manual"


class QueryRequest(BaseModel):
    query: str = Field(min_length=3, max_length=4000)
    thread_id: str = Field(default="default", min_length=1, max_length=100)


class Citation(BaseModel):
    document_id: str
    source: str
    chunk_id: int
    score: float
    text: str


class QueryResponse(BaseModel):
    answer: str
    plan: list[str]
    citations: list[Citation]
    review: str
    evaluation: EvaluationResult
    metadata: dict[str, Any] = Field(default_factory=dict)
