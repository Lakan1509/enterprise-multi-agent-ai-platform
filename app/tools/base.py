from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    handler: Callable[..., Any]

    def execute(self, **kwargs: Any) -> Any:
        """
        Execute the underlying tool handler.
        """
        return self.handler(**kwargs)
