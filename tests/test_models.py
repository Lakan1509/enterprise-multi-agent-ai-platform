"""Tests for casi.models.providers."""

import sys

import pytest

from casi.models import (
    Completion,
    CostTracker,
    LLMProvider,
    MockProvider,
    ModelRouter,
    OllamaProvider,
)


def test_mock_provider_deterministic() -> None:
    provider = MockProvider()
    first = provider.complete("You are a coder.", "Write a sort function.")
    second = provider.complete("You are a coder.", "Write a sort function.")
    assert first == second
    assert first.provider == "mock"
    assert first.model == "casi-mock-1"


def test_mock_provider_envelope_format() -> None:
    provider = MockProvider()
    completion = provider.complete("system prompt", "user prompt about sorting")
    assert completion.text.startswith("[mock:casi-mock-1] intent_detected=")
    assert "sorting" in completion.text
    assert completion.text.endswith("user prompt about sorting")


def test_mock_provider_token_estimates() -> None:
    provider = MockProvider()
    completion = provider.complete("abcd", "efgh")
    assert completion.prompt_tokens == len("abcd\nefgh") // 4
    assert completion.completion_tokens == len(completion.text) // 4


class _OtherProvider(LLMProvider):
    name = "other"

    def complete(self, system: str, user: str, **kwargs) -> Completion:
        return Completion(
            text="other-response",
            provider=self.name,
            model="other-1",
            prompt_tokens=1,
            completion_tokens=1,
        )


def test_router_routes_by_task_kind() -> None:
    router = ModelRouter(
        providers={"mock": MockProvider(), "other": _OtherProvider()},
        default="mock",
        routes={"summarize": "other"},
    )
    assert router.complete("summarize", "s", "u").provider == "other"
    assert router.complete("code", "s", "u").provider == "mock"  # default fallback
    assert router.available() == ["mock", "other"]


def test_router_unknown_provider_raises_value_error() -> None:
    router = ModelRouter(
        providers={"mock": MockProvider()},
        default="mock",
        routes={"broken": "no-such-provider"},
    )
    with pytest.raises(ValueError, match="no-such-provider"):
        router.complete("broken", "s", "u")


def test_router_unknown_default_raises_value_error() -> None:
    with pytest.raises(ValueError, match="nope"):
        ModelRouter(providers={"mock": MockProvider()}, default="nope")


def test_cost_tracker_totals() -> None:
    tracker = CostTracker()
    tracker.record(
        Completion(text="a", provider="mock", model="m", prompt_tokens=100, completion_tokens=50),
        cost_per_1k=2.0,
    )
    tracker.record(
        Completion(text="b", provider="other", model="m", prompt_tokens=200, completion_tokens=0),
        cost_per_1k=2.0,
    )
    assert tracker.total_tokens() == 350
    assert tracker.total_cost() == pytest.approx(350 / 1000 * 2.0)
    summary = tracker.summary()
    assert summary["total_tokens"] == 350
    assert summary["total_cost"] == pytest.approx(0.7)
    assert summary["by_provider"] == {"mock": 150, "other": 200}


def test_ollama_missing_package_raises_helpful_error(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "ollama", None)  # force ImportError
    provider = OllamaProvider(host="http://127.0.0.1:1", model="nope")
    with pytest.raises(RuntimeError, match="(?i)ollama"):
        provider.complete("s", "u")


def test_ollama_unreachable_host_raises_helpful_error() -> None:
    pytest.importorskip("ollama")  # only meaningful with the package installed
    provider = OllamaProvider(host="http://127.0.0.1:1", model="nope")
    with pytest.raises(RuntimeError, match="(?i)ollama"):
        provider.complete("s", "u")
