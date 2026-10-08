"""LLM provider seam for CASI.

:class:`LLMProvider` is the abstraction; :class:`MockProvider` is the
deterministic stand-in used by default; :class:`OllamaProvider` talks to a
local Ollama server over its REST API; :class:`NebiusProvider` talks to
Nebius AI Studio over its OpenAI-compatible chat-completions API;
:class:`ModelRouter` routes by task kind; :class:`CostTracker` accumulates
token usage and cost.

Network providers share one discipline:

* explicit **connect** and **read** timeouts on every request — no unbounded
  waits;
* up to 2 retries with exponential backoff on transient failures
  (connection errors, timeouts, HTTP 429 / 5xx);
* a single error taxonomy rooted at :class:`ProviderError`, so callers handle
  failures by type instead of parsing strings;
* secrets (API keys) are read from the environment at call time and never
  appear in code, logs, or exception messages.

Token counts are **estimates** (``len(text) // 4``) unless the provider
returns real usage figures (Ollama ``prompt_eval_count`` / ``eval_count``,
Nebius ``usage``), in which case the real counts are used.
"""

from __future__ import annotations

import abc
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

_TOKEN_RE = re.compile(r"\w+")


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ``len(text) // 4``. Not a real tokenizer count."""
    return len(text) // 4


def _coerce_int(value: Any) -> int | None:
    """Return ``value`` as a non-negative int, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


# ---------------------------------------------------------------------------
# Error taxonomy
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    """Base class for every error raised by CASI LLM providers."""


class ProviderConnectionError(ProviderError):
    """The provider endpoint could not be reached (DNS, refused, reset)."""


class ProviderTimeoutError(ProviderError):
    """A request exceeded its connect or read timeout."""


class ProviderResponseError(ProviderError):
    """The provider answered with an HTTP error or an unusable payload.

    ``transient`` marks failures worth retrying (HTTP 429 / 5xx).
    """

    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


class ProviderAuthError(ProviderResponseError):
    """The provider rejected the credentials (HTTP 401/403). Never retried."""

    def __init__(self, message: str) -> None:
        super().__init__(message, transient=False)


class ProviderConfigurationError(ValueError):
    """Bad provider configuration: missing API key, bad env value, unknown name.

    Subclasses :class:`ValueError` so existing ``ValueError`` handling for
    misconfiguration keeps working.
    """


# ---------------------------------------------------------------------------
# HTTP core: timeouts, retry with backoff, status mapping
# ---------------------------------------------------------------------------

#: HTTP statuses worth another attempt (after backoff).
_TRANSIENT_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

_MAX_RETRIES = 2  # total attempts per request = 1 + _MAX_RETRIES
_BACKOFF_BASE_SECONDS = 0.5  # attempt n (0-based) sleeps _BACKOFF_BASE_SECONDS * 2**n
_MAX_RETRY_AFTER_SECONDS = 10.0  # cap for honouring a Retry-After header


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

_PROXY_ENV_VARS = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
)


def _is_loopback_url(url: str) -> bool:
    """True when ``url`` targets localhost/loopback (must bypass any proxy)."""
    try:
        host = (httpx.URL(url).host or "").lower().strip("[]")
    except Exception:
        return False
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


def _env_proxy_url() -> str | None:
    """First non-empty proxy environment variable, or None."""
    for var in _PROXY_ENV_VARS:
        value = os.environ.get(var)
        if value and value.strip():
            return value.strip()
    return None


def _http_client(connect_timeout: float, read_timeout: float, url: str) -> httpx.Client:
    """Build an httpx client with explicit connect/read/write timeouts.

    Proxy policy (deliberate, documented):

    * ``trust_env=False`` always: proxy handling is explicit, because a
      malformed proxy/``no_proxy`` environment must never crash client
      construction, and loopback providers (Ollama) must bypass proxies
      deterministically.
    * Loopback targets (``localhost`` / ``127.x`` / ``::1``) go direct —
      no proxy, ever.
    * Non-loopback targets (e.g. Nebius) use the first usable
      ``HTTPS_PROXY``/``HTTP_PROXY``/``ALL_PROXY`` env var when present. A
      malformed proxy URL raises :class:`ProviderConfigurationError`
      *without echoing the URL* (it may embed credentials).

    * TLS uses httpx's default CA bundle (certifi) unless the standard
      ``SSL_CERT_FILE`` env var points at a PEM bundle — the usual escape
      hatch for MITM corporate/egress proxies with a private CA. A
      non-existent path raises :class:`ProviderConfigurationError`.

    Factored out (instead of inlined) so tests can monkeypatch the HTTP
    layer with :class:`httpx.MockTransport` without touching real sockets.
    """
    timeout = httpx.Timeout(
        connect=connect_timeout,
        read=read_timeout,
        write=read_timeout,
        pool=connect_timeout,
    )
    proxy: str | None = None
    if not _is_loopback_url(url):
        proxy = _env_proxy_url()
        if proxy is not None:
            try:
                if not httpx.URL(proxy).host:
                    raise ValueError("proxy URL has no host")
            except Exception as exc:
                # Never echo the proxy URL: it may contain credentials.
                raise ProviderConfigurationError(
                    "Unusable proxy URL in HTTPS_PROXY/HTTP_PROXY/ALL_PROXY "
                    f"environment variable: {exc}. Fix or unset it."
                ) from exc
    verify: str | bool = True
    ca_bundle = os.environ.get("SSL_CERT_FILE")
    if ca_bundle and ca_bundle.strip():
        ca_bundle = ca_bundle.strip()
        if not os.path.isfile(ca_bundle):
            raise ProviderConfigurationError(
                f"SSL_CERT_FILE points at {ca_bundle!r}, which does not exist."
            )
        verify = ca_bundle
    return httpx.Client(trust_env=False, proxy=proxy, timeout=timeout, verify=verify)


def _retry_delay(attempt: int, response: httpx.Response | None) -> float:
    """Backoff before retry ``attempt`` (0-based); honours a sane Retry-After."""
    if response is not None:
        raw = response.headers.get("retry-after")
        if raw:
            try:
                delay = float(raw)
            except ValueError:
                delay = 0.0
            if 0 < delay <= _MAX_RETRY_AFTER_SECONDS:
                return delay
    return _BACKOFF_BASE_SECONDS * (2**attempt)


def _safe_body_snippet(response: httpx.Response, limit: int = 300) -> str:
    """One-line snippet of an error body (never used for secrets)."""
    try:
        text = response.text
    except Exception:  # decoding should never break error reporting
        return ""
    text = " ".join(text.split())
    return f": {text[:limit]}" if text else ""


def _map_status_error(
    exc: httpx.HTTPStatusError, *, url: str, operation: str
) -> ProviderError:
    """Map an HTTP error status onto the provider error taxonomy."""
    status = exc.response.status_code
    detail = _safe_body_snippet(exc.response)
    where = f"{operation} ({url})"
    if status in (401, 403):
        # Never retried, and the message must not echo any credential.
        return ProviderAuthError(
            f"{where}: provider rejected credentials (HTTP {status}). "
            "Check the API key / server configuration."
        )
    if status in _TRANSIENT_HTTP_STATUSES:
        return ProviderResponseError(
            f"{where}: transient provider failure (HTTP {status}){detail}.",
            transient=True,
        )
    return ProviderResponseError(
        f"{where}: provider failure (HTTP {status}){detail}.",
        transient=False,
    )


def _parse_json_body(response: httpx.Response, *, url: str, operation: str) -> Any:
    """Parse a JSON body; a non-JSON 2xx is a (non-retryable) response error."""
    try:
        return response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProviderResponseError(
            f"{operation} ({url}): provider returned a non-JSON body "
            f"(HTTP {response.status_code}).",
            transient=False,
        ) from exc


def _is_retryable(error: ProviderError) -> bool:
    """Whether ``error`` is transient enough for another attempt."""
    if isinstance(error, (ProviderConnectionError, ProviderTimeoutError)):
        return True
    return isinstance(error, ProviderResponseError) and error.transient


def _request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    connect_timeout: float,
    read_timeout: float,
    operation: str,
) -> Any:
    """Send one JSON HTTP request; return the parsed JSON body.

    Applies explicit connect/read timeouts and up to ``_MAX_RETRIES``
    retries with backoff on transient failures. Raises the
    :class:`ProviderError` taxonomy. Header values (API keys) are never
    included in error messages.
    """
    last_error: ProviderError | None = None
    response: httpx.Response | None = None
    for attempt in range(_MAX_RETRIES + 1):
        response = None
        try:
            with _http_client(connect_timeout, read_timeout, url) as client:
                response = client.request(method, url, json=payload, headers=headers or {})
                response.raise_for_status()
                return _parse_json_body(response, url=url, operation=operation)
        except httpx.InvalidURL as exc:
            # Malformed OLLAMA_HOST / NEBIUS_BASE_URL. The target URL carries
            # no secrets (keys travel in headers), so echoing it is safe.
            raise ProviderConfigurationError(
                f"{operation}: malformed provider URL {url!r}: {exc}"
            ) from exc
        except httpx.ConnectTimeout as exc:
            last_error = ProviderTimeoutError(
                f"{operation}: connect timed out after {connect_timeout:g}s: {url}: {exc}"
            )
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as exc:
            last_error = ProviderTimeoutError(
                f"{operation}: timed out after {read_timeout:g}s waiting on {url}: {exc}"
            )
        except (httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            last_error = ProviderConnectionError(f"{operation}: could not reach {url}: {exc}")
        except httpx.HTTPStatusError as exc:
            last_error = _map_status_error(exc, url=url, operation=operation)
        except httpx.HTTPError as exc:  # any other transport/decoding problem
            last_error = ProviderConnectionError(
                f"{operation}: HTTP transport error for {url}: {exc}"
            )
        # Note: ProviderResponseError from _parse_json_body is intentionally
        # NOT caught here — a malformed 2xx body is not worth retrying.
        if last_error is not None and not _is_retryable(last_error):
            raise last_error
        if attempt < _MAX_RETRIES:
            time.sleep(_retry_delay(attempt, response))
    assert last_error is not None  # the loop only exits via return or with an error set
    raise last_error


# ---------------------------------------------------------------------------
# Completions
# ---------------------------------------------------------------------------


@dataclass
class Completion:
    """A single model completion.

    ``prompt_tokens`` / ``completion_tokens`` are char-based estimates
    (``len(text) // 4``) unless the provider reported real usage figures.
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

    Talks to the Ollama REST API (``POST /api/chat``, ``GET /api/tags``)
    with explicit connect/read timeouts and retry-with-backoff on transient
    failures. ``host`` / ``model`` default from the ``OLLAMA_HOST`` /
    ``OLLAMA_MODEL`` environment variables (falling back to
    ``http://127.0.0.1:11434`` / ``qwen2.5:0.5b``).

    Failures raise the :class:`ProviderError` taxonomy with actionable
    messages. When Ollama reports ``prompt_eval_count`` / ``eval_count``,
    those real token counts are used; otherwise char-based estimates.
    """

    name = "ollama"

    DEFAULT_HOST = "http://127.0.0.1:11434"
    DEFAULT_MODEL = "qwen2.5:0.5b"

    #: kwargs accepted by :meth:`complete` mapped to Ollama ``options`` keys.
    _OPTIONS_MAP = {
        "temperature": "temperature",
        "top_p": "top_p",
        "max_tokens": "num_predict",
    }

    def __init__(
        self,
        host: str | None = None,
        model: str | None = None,
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 120.0,
    ) -> None:
        self.host = (host or os.environ.get("OLLAMA_HOST") or self.DEFAULT_HOST).rstrip("/")
        self.model = model or os.environ.get("OLLAMA_MODEL") or self.DEFAULT_MODEL
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout

    def list_models(self) -> list[str]:
        """Model names the Ollama server reports via ``GET /api/tags``."""
        data = _request_json(
            "GET",
            f"{self.host}/api/tags",
            connect_timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
            operation=f"Ollama list_models at {self.host}",
        )
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            raise ProviderResponseError(
                f"Ollama list_models ({self.host}/api/tags): unexpected payload shape."
            )
        return [m.get("name", "") for m in models if isinstance(m, dict)]

    def complete(self, system: str, user: str, **kwargs) -> Completion:
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system or ""},
                {"role": "user", "content": user or ""},
            ],
        }
        options = {
            opt_key: kwargs[kw]
            for kw, opt_key in self._OPTIONS_MAP.items()
            if kwargs.get(kw) is not None
        }
        if options:
            payload["options"] = options
        data = _request_json(
            "POST",
            f"{self.host}/api/chat",
            payload=payload,
            connect_timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
            operation=f"Ollama chat (model={self.model!r})",
        )
        message = data.get("message") if isinstance(data, dict) else None
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str) or not text:
            raise ProviderResponseError(
                f"Ollama chat (model={self.model!r}): response carried no message content."
            )
        combined = f"{system or ''}\n{user or ''}"
        prompt_tokens = _coerce_int(data.get("prompt_eval_count")) or _estimate_tokens(combined)
        completion_tokens = _coerce_int(data.get("eval_count")) or _estimate_tokens(text)
        return Completion(
            text=text,
            provider=self.name,
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )


class NebiusProvider(LLMProvider):
    """LLM provider for Nebius AI Studio (``name="nebius"``).

    Speaks the OpenAI-compatible chat-completions API at
    ``{base_url}/chat/completions`` (default
    ``https://api.studio.nebius.com/v1``, overridable via ``NEBIUS_BASE_URL``).

    Authentication is **only** via the ``NEBIUS_API_KEY`` environment
    variable, read at call time — it is never stored on the instance, in
    code, or in logs, and never appears in exception messages. A missing or
    empty key raises :class:`ProviderConfigurationError` before any network
    traffic. The model comes from ``NEBIUS_MODEL`` (default
    ``meta-llama/Meta-Llama-3.1-8B-Instruct``, Nebius's documented
    HuggingFace-style model id).

    Same timeout / retry / error-taxonomy discipline as
    :class:`OllamaProvider`. When the API returns ``usage``, those real token
    counts are used; otherwise char-based estimates.
    """

    name = "nebius"

    DEFAULT_BASE_URL = "https://api.studio.nebius.com/v1"
    DEFAULT_MODEL = "meta-llama/Meta-Llama-3.1-8B-Instruct"

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        *,
        connect_timeout: float = 5.0,
        read_timeout: float = 60.0,
    ) -> None:
        self.model = model or os.environ.get("NEBIUS_MODEL") or self.DEFAULT_MODEL
        self.base_url = (
            base_url or os.environ.get("NEBIUS_BASE_URL") or self.DEFAULT_BASE_URL
        ).rstrip("/")
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout

    def _api_key(self) -> str:
        """Read ``NEBIUS_API_KEY`` at call time; never stored or logged."""
        key = os.environ.get("NEBIUS_API_KEY")
        if not key or not key.strip():
            raise ProviderConfigurationError(
                "NebiusProvider requires the NEBIUS_API_KEY environment variable "
                "to be set (e.g. `export NEBIUS_API_KEY=...`) before calling complete()."
            )
        return key.strip()

    def _auth_headers(self) -> dict[str, str]:
        # The key lives only in this local dict; error paths below never
        # format headers into messages.
        return {"Authorization": f"Bearer {self._api_key()}"}

    def list_models(self) -> list[str]:
        """Model ids the Nebius API reports via ``GET {base}/models``."""
        data = _request_json(
            "GET",
            f"{self.base_url}/models",
            headers=self._auth_headers(),
            connect_timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
            operation="Nebius list_models",
        )
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise ProviderResponseError(
                "Nebius list_models: unexpected payload shape (expected 'data' list)."
            )
        return [i.get("id", "") for i in items if isinstance(i, dict)]

    def complete(self, system: str, user: str, **kwargs) -> Completion:
        headers = self._auth_headers()  # local only; never logged or formatted
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system or ""},
                {"role": "user", "content": user or ""},
            ],
        }
        for key in ("temperature", "top_p", "max_tokens"):
            if kwargs.get(key) is not None:
                payload[key] = kwargs[key]
        data = _request_json(
            "POST",
            f"{self.base_url}/chat/completions",
            payload=payload,
            headers=headers,
            connect_timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
            operation=f"Nebius chat (model={self.model!r})",
        )
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise ProviderResponseError(
                f"Nebius chat (model={self.model!r}): response carried no choices."
            )
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str) or not text:
            raise ProviderResponseError(
                f"Nebius chat (model={self.model!r}): first choice carried no textual "
                "content (tool-call-only responses are not supported by this seam)."
            )
        usage = data.get("usage") if isinstance(data, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        combined = f"{system or ''}\n{user or ''}"
        prompt_tokens = _coerce_int(usage.get("prompt_tokens")) or _estimate_tokens(combined)
        completion_tokens = _coerce_int(usage.get("completion_tokens")) or _estimate_tokens(text)
        return Completion(
            text=text,
            provider=self.name,
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


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
        """Total prompt + completion tokens recorded (estimates unless provider reported real usage)."""
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


# ---------------------------------------------------------------------------
# Env-driven provider selection
# ---------------------------------------------------------------------------

#: Environment variable selecting the default provider.
PROVIDER_ENV_VAR = "CASI_DEFAULT_PROVIDER"

_KNOWN_PROVIDERS = ("mock", "ollama", "nebius")


def create_provider(name: str | None = None) -> LLMProvider:
    """Create a provider by name.

    ``name`` defaults to the ``CASI_DEFAULT_PROVIDER`` environment variable,
    which itself defaults to ``"mock"``. Names are matched case-insensitively.
    An unknown name raises :class:`ProviderConfigurationError` (a
    :class:`ValueError`) listing the valid choices.
    """
    resolved = (name or os.environ.get(PROVIDER_ENV_VAR) or "mock").strip().lower()
    if resolved == "mock":
        return MockProvider()
    if resolved == "ollama":
        return OllamaProvider()
    if resolved == "nebius":
        return NebiusProvider()
    raise ProviderConfigurationError(
        f"Unknown LLM provider {resolved!r} (from {PROVIDER_ENV_VAR}). "
        f"Expected one of: {', '.join(_KNOWN_PROVIDERS)}."
    )


def build_default_router(routes: dict[str, str] | None = None) -> ModelRouter:
    """Build a :class:`ModelRouter` with every known provider registered.

    The default provider is resolved from ``CASI_DEFAULT_PROVIDER`` via
    :func:`create_provider`; an unknown value raises
    :class:`ProviderConfigurationError` with the valid choices.
    """
    providers: dict[str, LLMProvider] = {
        "mock": MockProvider(),
        "ollama": OllamaProvider(),
        "nebius": NebiusProvider(),
    }
    default = create_provider().name
    return ModelRouter(providers=providers, default=default, routes=routes)
