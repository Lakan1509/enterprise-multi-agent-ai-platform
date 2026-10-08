"""CASI security: capabilities, approval gates, audit log, and API-key auth."""

from .approvals import Approval, ApprovalGate, ApprovalNotFound, ApprovalTimeout
from .audit import AuditLog
from .auth import verify_api_key
from .permissions import (
    ROLE_CAPABILITIES,
    Capability,
    PermissionDenied,
    Role,
    check,
)

__all__ = [
    "Approval",
    "ApprovalGate",
    "ApprovalNotFound",
    "ApprovalTimeout",
    "AuditLog",
    "Capability",
    "PermissionDenied",
    "Role",
    "ROLE_CAPABILITIES",
    "check",
    "verify_api_key",
]
