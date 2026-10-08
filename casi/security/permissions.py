"""Role-based capabilities. Fail closed: anything not granted is denied."""

from __future__ import annotations

from enum import Enum


class PermissionDenied(Exception):
    """Raised when a role lacks a capability (or the role is unknown)."""


class Capability(str, Enum):
    """Actions that can be gated by role."""

    EXECUTE_CODE = "EXECUTE_CODE"
    READ_WORKSPACE = "READ_WORKSPACE"
    WRITE_WORKSPACE = "WRITE_WORKSPACE"
    NETWORK = "NETWORK"
    APPROVE_PUBLISH = "APPROVE_PUBLISH"
    MANAGE_PLUGINS = "MANAGE_PLUGINS"
    APPROVE_OWN_RUNS = "APPROVE_OWN_RUNS"
    """Auto-approve a goal run's approval gate. ADMIN-only by design."""

    MANAGE_GOALS = "MANAGE_GOALS"
    """Create, run, and cancel goals via the API."""


class Role(str, Enum):
    """Principal roles."""

    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


ROLE_CAPABILITIES: dict[Role, set[Capability]] = {
    Role.ADMIN: set(Capability),
    Role.OPERATOR: {
        Capability.EXECUTE_CODE,
        Capability.READ_WORKSPACE,
        Capability.WRITE_WORKSPACE,
        Capability.NETWORK,
        Capability.MANAGE_GOALS,
    },
    Role.VIEWER: {Capability.READ_WORKSPACE},
}
"""Capabilities granted to each role. Unknown roles grant nothing.

``APPROVE_OWN_RUNS`` is granted only to :data:`Role.ADMIN` — it lets an
actor auto-approve a run's approval gate, so it must never leak to
operators or viewers.
"""


def check(role: Role, capability: Capability) -> None:
    """Assert ``role`` holds ``capability``.

    Raises:
        PermissionDenied: If the role lacks the capability, or the role is
            not a known :class:`Role`.
    """
    granted = ROLE_CAPABILITIES.get(role)  # type: ignore[arg-type]
    if granted is None or capability not in granted:
        raise PermissionDenied(f"role {role!r} lacks capability {capability!r}")
