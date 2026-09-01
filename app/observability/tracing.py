from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any


@dataclass
class ToolCallTrace:
    tool_name: str
    success: bool
    latency_ms: float
    error: str | None = None


@dataclass
class ExecutionTrace:
    request_id: str
    route: str | None = None
    retrieval_mode: str | None = None
    retry_count: int = 0
    total_latency_ms: float = 0.0
    grounded: bool | None = None
    hallucination_detected: bool | None = None
    tool_calls: list[ToolCallTrace] = field(default_factory=list)

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_calls)

    @property
    def tool_success_count(self) -> int:
        return sum(call.success for call in self.tool_calls)

    @property
    def tool_failure_count(self) -> int:
        return sum(not call.success for call in self.tool_calls)

    @property
    def tool_latency_ms(self) -> float:
        return sum(call.latency_ms for call in self.tool_calls)


def execute_traced_tool(
    *,
    trace: ExecutionTrace,
    tool_name: str,
    handler,
    **kwargs: Any,
) -> Any:
    start = perf_counter()

    try:
        result = handler(**kwargs)

    except Exception as exc:
        latency_ms = (perf_counter() - start) * 1000

        trace.tool_calls.append(
            ToolCallTrace(
                tool_name=tool_name,
                success=False,
                latency_ms=latency_ms,
                error=str(exc),
            )
        )

        raise

    latency_ms = (perf_counter() - start) * 1000

    trace.tool_calls.append(
        ToolCallTrace(
            tool_name=tool_name,
            success=True,
            latency_ms=latency_ms,
        )
    )

    return result
