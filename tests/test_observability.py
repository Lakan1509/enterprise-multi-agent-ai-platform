import pytest

from app.observability.tracing import (
    ExecutionTrace,
    execute_traced_tool,
)


def test_traced_tool_records_success():
    trace = ExecutionTrace(request_id="req-1")

    result = execute_traced_tool(
        trace=trace,
        tool_name="calculator",
        handler=lambda a, b: a + b,
        a=2,
        b=3,
    )

    assert result == 5
    assert trace.tool_call_count == 1
    assert trace.tool_success_count == 1
    assert trace.tool_failure_count == 0
    assert trace.tool_calls[0].tool_name == "calculator"
    assert trace.tool_calls[0].latency_ms >= 0


def test_traced_tool_records_failure():
    trace = ExecutionTrace(request_id="req-2")

    def fail():
        raise RuntimeError("tool failed")

    with pytest.raises(RuntimeError):
        execute_traced_tool(
            trace=trace,
            tool_name="broken_tool",
            handler=fail,
        )

    assert trace.tool_call_count == 1
    assert trace.tool_success_count == 0
    assert trace.tool_failure_count == 1
    assert trace.tool_calls[0].error == "tool failed"


def test_execution_trace_aggregates_tool_latency():
    trace = ExecutionTrace(request_id="req-3")

    execute_traced_tool(
        trace=trace,
        tool_name="one",
        handler=lambda: "ok",
    )

    execute_traced_tool(
        trace=trace,
        tool_name="two",
        handler=lambda: "ok",
    )

    assert trace.tool_call_count == 2
    assert trace.tool_latency_ms >= 0
