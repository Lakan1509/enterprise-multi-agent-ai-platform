"""Tests for casi.models.providers.

Unit tests only: **no real network**. The HTTP layer
(``casi.models.providers._http_client``) is monkeypatched to return
``httpx.Client`` instances backed by ``httpx.MockTransport``.
"""

import json
from typing import Callable

import httpx
import pytest

import casi.models.providers as providers_mod
from casi.models import (
    Completion,
    CostTracker,
    LLMProvider,
    MockProvider,
    ModelRouter,
    NebiusProvider,
    OllamaProvider,
    ProviderAuthError,
    ProviderConfigurationError,
    ProviderConnectionError,
    ProviderError,
    ProviderResponseError,
    ProviderTimeoutError,
    build_default_router,
    create_provider,
)

_FAKE_KEY = "sk-test-fake-key-12345"


# ---------------------------------------------------------------------------
# MockTransport helpers ("mock the HTTP layer")
# ---------------------------------------------------------------------------


def _client_factory(handler: Callable[[httpx.Request], httpx.Response]) -> Callable:
    """Replacement for ``providers._http_client`` routing through MockTransport."""

    def factory(connect_timeout: float, read_timeout: float, url: str) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(handler),
            timeout=httpx.Timeout(
                connect=connect_timeout,
                read=read_timeout,
                write=read_timeout,
                pool=connect_timeout,
            ),
        )

    return factory


def _json_handler(status: int, payload: dict) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


def _install(monkeypatch: pytest.MonkeyPatch, handler: Callable) -> dict:
    """Monkeypatch the HTTP layer; return a dict with the request ``count``."""
    calls = {"count": 0}

    def counting(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return handler(request)

    monkeypatch.setattr(providers_mod, "_http_client", _client_factory(counting))
    return calls


def _flaky_handler(sequence: list[tuple[int, dict]]) -> Callable:
    """Serve ``(status, payload)`` pairs in order; repeat the last one."""
    remaining = list(sequence)

    def handler(request: httpx.Request) -> httpx.Response:
        status, payload = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        return httpx.Response(status, json=payload)

    return handler


# ---------------------------------------------------------------------------
# MockProvider (unchanged behaviour)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Provider selection via CASI_DEFAULT_PROVIDER
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, cls",
    [("mock", MockProvider), ("ollama", OllamaProvider), ("nebius", NebiusProvider)],
)
def test_create_provider_by_name(name: str, cls: type) -> None:
    assert isinstance(create_provider(name), cls)


def test_create_provider_reads_env_case_insensitively(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CASI_DEFAULT_PROVIDER", "Ollama")
    assert isinstance(create_provider(), OllamaProvider)


def test_create_provider_defaults_to_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CASI_DEFAULT_PROVIDER", raising=False)
    assert isinstance(create_provider(), MockProvider)


def test_create_provider_unknown_name_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CASI_DEFAULT_PROVIDER", "watson")
    with pytest.raises(ProviderConfigurationError, match="CASI_DEFAULT_PROVIDER"):
        create_provider()
    # ProviderConfigurationError is a ValueError for back-compat.
    with pytest.raises(ValueError, match="watson"):
        create_provider("watson")


def test_build_default_router_registers_all_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CASI_DEFAULT_PROVIDER", "mock")
    router = build_default_router()
    assert router.available() == ["mock", "nebius", "ollama"]
    assert router.complete("anything", "s", "u").provider == "mock"


def test_build_default_router_unknown_env_value_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CASI_DEFAULT_PROVIDER", "nope")
    with pytest.raises(ProviderConfigurationError, match="nope"):
        build_default_router()


def test_build_default_router_routes_to_env_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CASI_DEFAULT_PROVIDER", "ollama")
    _install(monkeypatch, _json_handler(200, {"message": {"content": "real-ish"}}))
    router = build_default_router(routes={"code": "ollama"})
    completion = router.complete("code", "s", "u")
    assert completion.provider == "ollama"
    assert completion.text == "real-ish"


# ---------------------------------------------------------------------------
# OllamaProvider
# ---------------------------------------------------------------------------


def test_ollama_chat_success_uses_reported_token_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install(
        monkeypatch,
        _json_handler(
            200,
            {
                "message": {"role": "assistant", "content": "hello there"},
                "prompt_eval_count": 10,
                "eval_count": 5,
            },
        ),
    )
    completion = OllamaProvider(host="http://x:11434", model="m").complete("s", "u")
    assert completion.text == "hello there"
    assert (completion.prompt_tokens, completion.completion_tokens) == (10, 5)
    assert completion.provider == "ollama"
    assert completion.model == "m"
    assert calls["count"] == 1


def test_ollama_chat_falls_back_to_estimates_without_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _json_handler(200, {"message": {"content": "hi"}}))
    completion = OllamaProvider(host="http://x", model="m").complete("abcd", "efgh")
    assert completion.prompt_tokens == len("abcd\nefgh") // 4
    assert completion.completion_tokens == len("hi") // 4


def test_ollama_kwargs_map_to_options_object(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "ok"}})

    _install(monkeypatch, handler)
    OllamaProvider(host="http://x", model="m").complete(
        "s", "u", temperature=0.2, max_tokens=50
    )
    assert seen["options"] == {"temperature": 0.2, "num_predict": 50}
    assert seen["stream"] is False


def test_ollama_list_models(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        _json_handler(200, {"models": [{"name": "qwen2.5:0.5b"}, {"name": "other:1b"}]}),
    )
    assert OllamaProvider(host="http://x").list_models() == ["qwen2.5:0.5b", "other:1b"]


def test_ollama_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "http://env-host:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "env-model")
    provider = OllamaProvider()
    assert provider.host == "http://env-host:11434"
    assert provider.model == "env-model"


# ---------------------------------------------------------------------------
# Retry / timeout / error-taxonomy behaviour (mocked HTTP)
# ---------------------------------------------------------------------------


def test_transient_5xx_retried_then_success(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(
        monkeypatch,
        _flaky_handler(
            [
                (503, {"error": "unavailable"}),
                (503, {"error": "unavailable"}),
                (200, {"message": {"content": "ok"}}),
            ]
        ),
    )
    completion = OllamaProvider(host="http://x", model="m").complete("s", "u")
    assert completion.text == "ok"
    assert calls["count"] == 3  # 1 initial + 2 retries


def test_persistent_5xx_raises_after_retries_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install(monkeypatch, _json_handler(500, {"error": "boom"}))
    with pytest.raises(ProviderResponseError, match="500"):
        OllamaProvider(host="http://x", model="m").complete("s", "u")
    assert calls["count"] == 3


def test_429_is_transient_and_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(
        monkeypatch,
        _flaky_handler(
            [
                (429, {"error": "rate limited"}),
                (200, {"message": {"content": "recovered"}}),
            ]
        ),
    )
    completion = OllamaProvider(host="http://x", model="m").complete("s", "u")
    assert completion.text == "recovered"
    assert calls["count"] == 2


def test_401_raises_auth_error_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEBIUS_API_KEY", _FAKE_KEY)
    calls = _install(monkeypatch, _json_handler(401, {"error": "bad key"}))
    with pytest.raises(ProviderAuthError, match="rejected credentials"):
        NebiusProvider(model="m").complete("s", "u")
    assert calls["count"] == 1


def test_4xx_other_than_auth_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, _json_handler(400, {"error": "bad request"}))
    with pytest.raises(ProviderResponseError, match="400") as excinfo:
        OllamaProvider(host="http://x", model="m").complete("s", "u")
    assert excinfo.value.transient is False
    assert calls["count"] == 1


def test_connection_error_maps_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    calls = _install(monkeypatch, handler)
    with pytest.raises(ProviderConnectionError, match="could not reach"):
        OllamaProvider(host="http://127.0.0.1:1", model="nope").complete("s", "u")
    assert calls["count"] == 3


def test_read_timeout_maps_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    calls = _install(monkeypatch, handler)
    with pytest.raises(ProviderTimeoutError, match="timed out"):
        OllamaProvider(host="http://x", model="m", read_timeout=2).complete("s", "u")
    assert calls["count"] == 3


def test_connect_timeout_message_names_connect_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    _install(monkeypatch, handler)
    with pytest.raises(ProviderTimeoutError, match="connect timed out"):
        OllamaProvider(host="http://x", model="m", connect_timeout=1).complete("s", "u")


def test_malformed_json_body_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(
        monkeypatch, lambda request: httpx.Response(200, content=b"not json")
    )
    with pytest.raises(ProviderResponseError, match="non-JSON"):
        OllamaProvider(host="http://x", model="m").complete("s", "u")
    assert calls["count"] == 1


def test_missing_message_content_raises_response_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install(monkeypatch, _json_handler(200, {"message": {}}))
    with pytest.raises(ProviderResponseError, match="no message content"):
        OllamaProvider(host="http://x", model="m").complete("s", "u")
    assert calls["count"] == 1


# ---------------------------------------------------------------------------
# NebiusProvider
# ---------------------------------------------------------------------------


def test_nebius_missing_key_raises_before_any_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    calls = _install(monkeypatch, _json_handler(200, {}))
    with pytest.raises(ProviderConfigurationError, match="NEBIUS_API_KEY"):
        NebiusProvider().complete("s", "u")
    assert calls["count"] == 0


def test_nebius_blank_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEBIUS_API_KEY", "   ")
    with pytest.raises(ProviderConfigurationError, match="NEBIUS_API_KEY"):
        NebiusProvider().list_models()


def test_nebius_chat_success_uses_usage_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEBIUS_API_KEY", _FAKE_KEY)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "hi there"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3},
            },
        )

    _install(monkeypatch, handler)
    provider = NebiusProvider(model="meta-llama/Meta-Llama-3.1-8B-Instruct")
    completion = provider.complete("s", "u", temperature=0.5)
    assert seen["auth"] == f"Bearer {_FAKE_KEY}"
    assert seen["body"]["model"] == "meta-llama/Meta-Llama-3.1-8B-Instruct"
    assert seen["body"]["temperature"] == 0.5
    assert (completion.prompt_tokens, completion.completion_tokens) == (7, 3)
    assert completion.provider == "nebius"


def test_nebius_chat_falls_back_to_estimates_without_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEBIUS_API_KEY", _FAKE_KEY)
    _install(
        monkeypatch,
        _json_handler(200, {"choices": [{"message": {"content": "yo"}}]}),
    )
    completion = NebiusProvider(model="m").complete("abcd", "efgh")
    assert completion.prompt_tokens == len("abcd\nefgh") // 4


def test_nebius_empty_choices_raises_response_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEBIUS_API_KEY", _FAKE_KEY)
    _install(monkeypatch, _json_handler(200, {"choices": []}))
    with pytest.raises(ProviderResponseError, match="no choices"):
        NebiusProvider(model="m").complete("s", "u")


def test_nebius_tool_call_only_response_raises_response_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEBIUS_API_KEY", _FAKE_KEY)
    _install(
        monkeypatch,
        _json_handler(
            200,
            {"choices": [{"message": {"content": None, "tool_calls": [{"id": "1"}]}}]},
        ),
    )
    with pytest.raises(ProviderResponseError, match="no textual content"):
        NebiusProvider(model="m").complete("s", "u")


def test_nebius_list_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEBIUS_API_KEY", _FAKE_KEY)
    _install(monkeypatch, _json_handler(200, {"data": [{"id": "a"}, {"id": "b"}]}))
    assert NebiusProvider().list_models() == ["a", "b"]


@pytest.mark.parametrize("status", [401, 500])
def test_nebius_key_never_appears_in_error_messages(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    monkeypatch.setenv("NEBIUS_API_KEY", _FAKE_KEY)
    _install(monkeypatch, _json_handler(status, {"error": "x"}))
    with pytest.raises(ProviderError) as excinfo:
        NebiusProvider().complete("s", "u")
    assert _FAKE_KEY not in str(excinfo.value)


def test_nebius_key_never_appears_in_connection_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEBIUS_API_KEY", _FAKE_KEY)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    _install(monkeypatch, handler)
    with pytest.raises(ProviderConnectionError) as excinfo:
        NebiusProvider().complete("s", "u")
    assert _FAKE_KEY not in str(excinfo.value)


def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        monkeypatch.delenv(var, raising=False)


def test_http_client_loopback_bypasses_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
    client = providers_mod._http_client(1.0, 2.0, "http://127.0.0.1:11434/api/tags")
    assert dict(client._mounts) == {}
    client.close()


def test_http_client_external_host_uses_env_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://user:pass@proxy.internal:3128")
    client = providers_mod._http_client(
        1.0, 2.0, "https://api.studio.nebius.com/v1/chat/completions"
    )
    assert set(client._mounts) != set()  # a proxy transport is mounted
    client.close()


def test_http_client_malformed_proxy_raises_without_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://user:s3cr3t-fake@[::1")
    with pytest.raises(ProviderConfigurationError, match="[Pp]roxy") as excinfo:
        providers_mod._http_client(
            1.0, 2.0, "https://api.studio.nebius.com/v1/chat/completions"
        )
    assert "s3cr3t-fake" not in str(excinfo.value)


def test_malformed_provider_url_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_proxy_env(monkeypatch)  # hermetic: no proxy involved
    with pytest.raises(ProviderConfigurationError, match="malformed provider URL"):
        OllamaProvider(host="http://[::1", model="m").list_models()


def test_http_client_missing_ssl_cert_file_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent/ca-bundle.pem")
    with pytest.raises(ProviderConfigurationError, match="SSL_CERT_FILE"):
        providers_mod._http_client(1.0, 2.0, "https://example.com/x")


def test_http_client_accepts_valid_ssl_cert_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import certifi

    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("SSL_CERT_FILE", certifi.where())
    client = providers_mod._http_client(1.0, 2.0, "https://example.com/x")
    client.close()


def test_nebius_default_model_id_format() -> None:
    assert (
        NebiusProvider.DEFAULT_MODEL == "meta-llama/Meta-Llama-3.1-8B-Instruct"
    )
    assert NebiusProvider.DEFAULT_BASE_URL == "https://api.studio.nebius.com/v1"


# ---------------------------------------------------------------------------
# Router / cost tracker (unchanged behaviour)
# ---------------------------------------------------------------------------


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
