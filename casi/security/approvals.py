"""Human-in-the-loop approval gates.

An :class:`ApprovalGate` holds in-memory :class:`Approval` records behind a
:class:`threading.Condition`: one thread :meth:`request`\\ s an action, the
kernel :meth:`wait`\\ s on it, and a human (or API call) :meth:`resolve`\\ s
it from another thread. A rejected approval surfaces as
:class:`PermissionDenied` so downstream code fails closed.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .permissions import PermissionDenied

#: Signature of the optional audit hook: ``hook(event_name, details_dict)``.
AuditHook = Callable[[str, dict[str, Any]], None]


class ApprovalNotFound(Exception):
    """Raised when no approval exists for the given id."""


class ApprovalTimeout(Exception):
    """Raised when :meth:`ApprovalGate.wait` exceeds its timeout."""


@dataclass
class Approval:
    """A single approval request and its resolution."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    action: str = ""
    details: dict = field(default_factory=dict)
    status: str = "pending"  # "pending" | "approved" | "rejected"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    resolved_at: str | None = None
    note: str = ""


class ApprovalGate:
    """Thread-safe in-memory approval gate.

    Args:
        audit_hook: Optional callable ``(event, details)`` invoked for
            ``approval.requested`` / ``approval.resolved`` events so the
            kernel's audit log sees every gate transition. May be ``None``.
    """

    def __init__(self, audit_hook: AuditHook | None = None) -> None:
        self._cond = threading.Condition()
        self._approvals: dict[str, Approval] = {}
        self._audit_hook = audit_hook

    def _emit(self, event: str, details: dict[str, Any]) -> None:
        """Fire the audit hook; hook failures must never break the gate."""
        if self._audit_hook is None:
            return
        try:
            self._audit_hook(event, details)
        except Exception:  # noqa: BLE001 - auditing is best-effort
            pass

    def request(self, action: str, details: dict) -> Approval:
        """Create a pending approval for ``action`` and return it."""
        approval = Approval(action=action, details=dict(details))
        with self._cond:
            self._approvals[approval.id] = approval
            self._cond.notify_all()
        self._emit(
            "approval.requested",
            {
                "approval_id": approval.id,
                "action": action,
                **({"goal_id": details["goal_id"]} if "goal_id" in details else {}),
            },
        )
        return approval

    def resolve(self, approval_id: str, approved: bool, note: str = "") -> Approval:
        """Resolve a pending approval as approved/rejected.

        Raises:
            ApprovalNotFound: If ``approval_id`` is unknown.
            ValueError: If the approval was already resolved.
        """
        with self._cond:
            approval = self._approvals.get(approval_id)
            if approval is None:
                raise ApprovalNotFound(f"unknown approval id: {approval_id!r}")
            if approval.status != "pending":
                raise ValueError(f"approval {approval_id!r} already resolved as {approval.status!r}")
            approval.status = "approved" if approved else "rejected"
            approval.resolved_at = datetime.now(timezone.utc).isoformat()
            approval.note = note
            self._cond.notify_all()
            event_details = {
                "approval_id": approval.id,
                "action": approval.action,
                "status": approval.status,
                "note": note,
            }
            if "goal_id" in approval.details:
                event_details["goal_id"] = approval.details["goal_id"]
        self._emit("approval.resolved", event_details)
        return approval

    def get(self, approval_id: str) -> Approval:
        """Return the approval with ``approval_id``.

        Raises:
            ApprovalNotFound: If ``approval_id`` is unknown.
        """
        with self._cond:
            approval = self._approvals.get(approval_id)
            if approval is None:
                raise ApprovalNotFound(f"unknown approval id: {approval_id!r}")
            return approval

    def pending(self) -> list[Approval]:
        """Return all approvals still awaiting resolution."""
        with self._cond:
            return [a for a in self._approvals.values() if a.status == "pending"]

    def wait(self, approval_id: str, timeout_s: float = 3600, poll_s: float = 0.5) -> Approval:
        """Block until the approval is resolved, then return it.

        Raises:
            ApprovalNotFound: If ``approval_id`` is unknown.
            ApprovalTimeout: If ``timeout_s`` elapses while still pending.
            PermissionDenied: If the approval was rejected.
        """
        with self._cond:
            approval = self._approvals.get(approval_id)
            if approval is None:
                raise ApprovalNotFound(f"unknown approval id: {approval_id!r}")
            deadline = time.monotonic() + timeout_s
            while approval.status == "pending":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ApprovalTimeout(
                        f"approval {approval_id!r} not resolved within {timeout_s}s"
                    )
                self._cond.wait(timeout=min(poll_s, remaining))
            if approval.status == "rejected":
                raise PermissionDenied(f"approval {approval_id!r} was rejected")
            return approval
