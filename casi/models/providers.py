"""LLM provider seam for CASI.

:class:`LLMProvider` is the abstraction; :class:`MockProvider` is the
deterministic stand-in used by default; :class:`OllamaProvider` talks to a
local Ollama server when available; :class:`ModelRouter` routes by task kind;
:class:`CostTracker` accumulates token usage and cost.

Token counts are **estimates** (``len(text) // 4``), not real tokenizer
counts — documented here and wherever estimates are produced.
"""

from __future__ import annotations

import abc
import re
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"\w+")


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ``len(text) // 4``. Not a real tokenizer count."""
    return len(text) // 4


@dataclass
class Completion:
    """A single model completion.

    ``prompt_tokens`` / ``completion_tokens`` are char-based estimates
    (``len(text) // 4``), not real tokenizer counts.
    """

    text: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int


class LLMProvider(abc.ABC):
    """Abstract LLM provider. ``name`` identifies the provider in the router."""

    name: str

    @abc.abstractmethod
    def complete(self, system: str, user: str, **kwargs) -> Completion:
        """Produce a completion for a (system, user) prompt pair."""
        raise NotImplementedError


class MockProvider(LLMProvider):
    """Deterministic placeholder provider (``name="mock"``, ``model="casi-mock-1"``).

    **MockProvider does not reason.** It returns a deterministic JSON-ish
    envelope built from keyword detection over the (system + user) text:

        ``[mock:casi-mock-1] intent_detected=<keywords> :: <first 200 chars of user>``

    where ``<keywords>`` is the sorted, comma-joined set of distinct tokens
    (length >= 3) found in the prompt. Agents use their own deterministic
    logic and treat this output as a placeholder; it is swappable for a real
    model later. Same input always yields the same output.
    """

    name = "mock"
    model = "casi-mock-1"

    def complete(self, system: str, user: str, **kwargs) -> Completion:
        system = system or ""
        user = user or ""
        combined = f"{system}\n{user}"
        lowered = combined.lower()
        keywords = sorted({t for t in _TOKEN_RE.findall(lowered) if len(t) >= 3})
        snippet = user[:200].replace("\n", " ")
        text = f"[mock:{self.model}] intent_detected={','.join(keywords)} :: {snippet}"
        return Completion(
            text=text,
            provider=self.name,
            model=self.model,
            prompt_tokens=_estimate_tokens(combined),
            completion_tokens=_estimate_tokens(text),
        )


class OllamaProvider(LLMProvider):
    """LLM provider backed by a local Ollama server (``name="ollama"``).

    The ``ollama`` Python package is imported lazily inside :meth:`complete`,
    so this module imports fine without it. Failures raise
    :class:`RuntimeError` with an actionable message.
    """

    name = "ollama"

    def __init__(self, host: str, model: str) -> None:
        self.host = host
        self.model = model

    def complete(self, system: str, user: str, **kwargs) -> Completion:
        try:
            import ollama  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "OllamaProvider requires the 'ollama' Python package and a running "
                "server: run 'pip install ollama', then ensure 'ollama serve' is "
                "running and the model is pulled ('ollama pull <model>')."
            ) from exc
        try:
            client = ollama.Client(host=self.host)
            response = client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": system or ""},
                    {"role": "user", "content": user or ""},
                ],
            )
            message = response["message"] if isinstance(response, dict) else response.message
            text = message["content"] if isinstance(message, dict) else message.content
        except Exception as exc:
            raise RuntimeError(
                f"Ollama request failed (host={self.host!r}, model={self.model!r}): "
                "ensure 'ollama serve' is running, the host is reachable, and the "
                f"model is pulled ('ollama pull {self.model}'). Original error: {exc}"
            ) from exc
        combined = f"{system or ''}\n{user or ''}"
        return Completion(
            text=text,
            provider=self.name,
            model=self.model,
            prompt_tokens=_estimate_tokens(combined),
            completion_tokens=_estimate_tokens(text),
        )


class ModelRouter:
    """Route completions to providers by task kind.

    ``routes`` maps ``task_kind -> provider name``; kinds not present fall
    back to ``default``. Resolving to an unknown provider raises
    :class:`ValueError`.
    """

    def __init__(
        self,
        providers: dict[str, LLMProvider],
        default: str = "mock",
        routes: dict[str, str] | None = None,
    ) -> None:
        if default not in providers:
            raise ValueError(f"Unknown default provider: {default!r}")
        self._providers = dict(providers)
        self._default = default
        self._routes = dict(routes) if routes else {}

    def complete(self, task_kind: str, system: str, user: str, **kwargs) -> Completion:
        """Complete via the provider routed for ``task_kind`` (or the default)."""
        name = self._routes.get(task_kind, self._default)
        provider = self._providers.get(name)
        if provider is None:
            raise ValueError(f"Unknown provider: {name!r}")
        return provider.complete(system, user, **kwargs)

    def available(self) -> list[str]:
        """Sorted names of all registered providers."""
        return sorted(self._providers.keys())


class CostTracker:
    """Accumulate token usage and cost across completions."""

    def __init__(self) -> None:
        self._tokens = 0
        self._cost = 0.0
        self._by_provider: dict[str, int] = {}

    def record(self, completion: Completion, cost_per_1k: float = 0.0) -> None:
        """Record a completion; ``cost_per_1k`` is the price per 1000 tokens."""
        tokens = completion.prompt_tokens + completion.completion_tokens
        self._tokens += tokens
        self._cost += (tokens / 1000.0) * cost_per_1k
        self._by_provider[completion.provider] = self._by_provider.get(completion.provider, 0) + tokens

    def total_tokens(self) -> int:
        """Total prompt + completion tokens recorded (estimates)."""
        return self._tokens

    def total_cost(self) -> float:
        """Total accumulated cost."""
        return self._cost

    def summary(self) -> dict:
        """``{"total_tokens": int, "total_cost": float, "by_provider": {name: tokens}}``."""
        return {
            "total_tokens": self._tokens,
            "total_cost": self._cost,
            "by_provider": dict(self._by_provider),
        }
