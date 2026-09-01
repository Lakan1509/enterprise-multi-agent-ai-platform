from dataclasses import dataclass
from typing import Any, Callable

from app.observability.tracing import (
    ExecutionTrace,
    execute_traced_tool,
)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    handler: Callable[..., Any]

    def execute(
        self,
        *,
        trace: ExecutionTrace | None = None,
        **kwargs: Any,
    ) -> Any:
        """
        Execute the underlying tool handler.

        When a trace is supplied, record tool success/failure and latency.
        Otherwise execute normally.
        """

        if trace is None:
            return self.handler(**kwargs)

        return execute_traced_tool(
            trace=trace,
            tool_name=self.name,
            handler=self.handler,
            **kwargs,
        )
