from pydantic import BaseModel, Field


class EvaluationResult(BaseModel):
    task_success: bool
    grounded: bool
    hallucination_detected: bool

    retry_count: int = Field(default=0, ge=0)
    latency_ms: float = Field(ge=0)

    tool_calls: int = Field(default=0, ge=0)
    tool_success_count: int = Field(default=0, ge=0)
    tool_failure_count: int = Field(default=0, ge=0)
    tool_latency_ms: float = Field(default=0.0, ge=0)
