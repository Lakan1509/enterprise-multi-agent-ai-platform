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
    },
    Role.VIEWER: {Capability.READ_WORKSPACE},
}
"""Capabilities granted to each role. Unknown roles grant nothing."""


def check(role: Role, capability: Capability) -> None:
    """Assert ``role`` holds ``capability``.

    Raises:
        PermissionDenied: If the role lacks the capability, or the role is
            not a known :class:`Role`.
    """
    granted = ROLE_CAPABILITIES.get(role)  # type: ignore[arg-type]
    if granted is None or capability not in granted:
        raise PermissionDenied(f"role {role!r} lacks capability {capability!r}")
