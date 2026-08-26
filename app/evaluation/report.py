from pydantic import BaseModel, Field

from app.evaluation.golden import GoldenTaskResult


class BatchEvaluationReport(BaseModel):
    total_tasks: int = Field(ge=0)
    passed_tasks: int = Field(ge=0)

    pass_rate: float = Field(ge=0, le=1)
    task_success_rate: float = Field(ge=0, le=1)
    groundedness_rate: float = Field(ge=0, le=1)
    hallucination_rate: float = Field(ge=0, le=1)

    average_latency_ms: float = Field(ge=0)
    p95_latency_ms: float = Field(ge=0)
    average_retries: float = Field(ge=0)
    average_tool_calls: float = Field(ge=0)

    results: list[GoldenTaskResult] = Field(default_factory=list)
