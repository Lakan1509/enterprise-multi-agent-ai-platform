"""API-key authentication as a FastAPI dependency.

:func:`verify_api_key` reads ``CASI_API_KEY`` from
:mod:`casi.config` ``Settings`` (imported lazily *inside* the function so this
module imports standalone, even without ``casi.config`` or ``fastapi``
installed — in which case it falls back to the ``CASI_API_KEY`` environment
variable). ``fastapi`` itself is also imported lazily inside the function.

Semantics:

* API key set and ``X-API-Key`` header matches  -> :data:`Role.OPERATOR`
* API key set and header missing/mismatched     -> ``HTTPException`` 401
* API key unset                                 -> :data:`Role.VIEWER`, with a
  one-time stderr warning that auth is disabled.
"""

from __future__ import annotations

import os
import sys

from fastapi import HTTPException, Request

from .permissions import Role

_warned_no_key = False


def _warn_once(message: str) -> None:
    """Print a stderr warning at most once per process."""
    global _warned_no_key
    if not _warned_no_key:
        _warned_no_key = True
        print(f"CASI auth: {message}", file=sys.stderr)


def _get_configured_api_key() -> str:
    """Return the configured ``CASI_API_KEY`` (``""`` when unset).

    Prefers :class:`casi.config.Settings`; falls back to the environment if
    ``casi.config`` is unavailable (standalone import).
    """
    try:
        from casi.config import Settings

        return getattr(Settings(), "api_key", "") or ""
    except Exception:
        return os.environ.get("CASI_API_KEY", "")


def verify_api_key(request: Request) -> Role:
    """FastAPI dependency: authenticate via the ``X-API-Key`` header.

    Args:
        request: The Starlette/FastAPI request.

    Returns:
        The caller's :class:`Role`.

    Raises:
        HTTPException: 401 when an API key is configured but the request's
            ``X-API-Key`` header does not match it.
    """
    api_key = _get_configured_api_key()
    if api_key:
        provided = request.headers.get("X-API-Key", "")
        if provided != api_key:
            raise HTTPException(status_code=401, detail="Invalid API key")
        return Role.OPERATOR
    _warn_once("CASI_API_KEY is not set; API auth is disabled, all callers are VIEWER.")
    return Role.VIEWER
