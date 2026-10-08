"""API-key authentication as a FastAPI dependency.

:func:`verify_api_key` reads the API keys from :mod:`casi.config`
``Settings`` (imported lazily *inside* the function so this module imports
standalone, even without ``casi.config`` or ``fastapi`` installed — in
which case it falls back to the ``CASI_API_KEY`` environment variable).
``fastapi`` itself is also imported lazily inside the function.

Key -> role mapping (documented, fail closed):

* ``CASI_API_KEY`` (admin key) matches          -> :data:`Role.ADMIN`
* ``CASI_OPERATOR_API_KEY`` matches             -> :data:`Role.OPERATOR`
* ``CASI_VIEWER_API_KEY`` matches               -> :data:`Role.VIEWER`
* any key configured but header missing/unknown -> ``HTTPException`` 401
* no key configured at all                      -> :data:`Role.VIEWER`, with a
  one-time stderr warning that auth is disabled (loopback dev mode only;
  :meth:`casi.config.Settings.validate_deployment` refuses to start
  non-loopback or ``CASI_REQUIRE_AUTH=1`` deployments without a strong key).
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


def _get_settings() -> object | None:
    """Return a :class:`casi.config.Settings` instance, or None standalone."""
    try:
        from casi.config import Settings

        return Settings()
    except Exception:
        return None


def _configured_keys() -> dict[Role, str]:
    """Map each role to its configured key (``""`` when unset)."""
    settings = _get_settings()
    if settings is not None:
        return {
            Role.ADMIN: getattr(settings, "api_key", "") or "",
            Role.OPERATOR: getattr(settings, "operator_api_key", "") or "",
            Role.VIEWER: getattr(settings, "viewer_api_key", "") or "",
        }
    # Standalone fallback: only the legacy single key is known.
    return {
        Role.ADMIN: os.environ.get("CASI_API_KEY", ""),
        Role.OPERATOR: "",
        Role.VIEWER: "",
    }


def verify_api_key(request: Request) -> Role:
    """FastAPI dependency: authenticate via the ``X-API-Key`` header.

    Args:
        request: The Starlette/FastAPI request.

    Returns:
        The caller's :class:`Role` per the key -> role mapping above.

    Raises:
        HTTPException: 401 when at least one API key is configured but the
            request's ``X-API-Key`` header does not match any of them.
    """
    keys = _configured_keys()
    if any(keys.values()):
        provided = request.headers.get("X-API-Key", "")
        for role, configured in keys.items():
            if configured and provided == configured:
                return role
        raise HTTPException(status_code=401, detail="Invalid API key")
    _warn_once("CASI_API_KEY is not set; API auth is disabled, all callers are VIEWER.")
    return Role.VIEWER
