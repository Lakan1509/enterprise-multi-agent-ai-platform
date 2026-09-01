from pydantic import BaseModel, Field


class GoldenTask(BaseModel):
    task_id: str = Field(min_length=1)
    query: str = Field(min_length=3)

    expected_route: str | None = None
    expected_answer_contains: list[str] = Field(default_factory=list)
    expected_citations: list[str] = Field(default_factory=list)

    max_retries: int = Field(default=2, ge=0)
    max_latency_ms: float | None = Field(default=None, gt=0)


class GoldenTaskResult(BaseModel):
    task_id: str
    passed: bool

    answer_match: bool
    route_match: bool
    citation_match: bool
    retry_limit_passed: bool
    latency_limit_passed: bool

    task_success: bool
    grounded: bool
    hallucination_detected: bool

    retry_count: int
    latency_ms: float
    tool_calls: int
