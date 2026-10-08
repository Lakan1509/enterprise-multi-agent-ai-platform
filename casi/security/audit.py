"""Append-only audit log backed by a JSONL file (thread-safe)."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLog:
    """JSONL audit log. One JSON object per line; entries are newest-last."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        """Filesystem path of the backing JSONL file."""
        return self._path

    def record(
        self,
        event: str,
        goal_id: str = "",
        task_id: str = "",
        actor: str = "",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append an audit entry and return it.

        The entry is ``{"ts", "event", "goal_id", "task_id", "actor",
        "details"}`` with ``ts`` as an ISO-8601 UTC timestamp.
        """
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "goal_id": goal_id,
            "task_id": task_id,
            "actor": actor,
            "details": dict(details) if details else {},
        }
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
        return entry

    def read(self, goal_id: str = "", limit: int = 200) -> list[dict[str, Any]]:
        """Return up to ``limit`` entries, newest last.

        When ``goal_id`` is given, only entries for that goal are returned.
        A missing log file reads as ``[]``.
        """
        with self._lock:
            if not self._path.exists():
                return []
            with self._path.open("r", encoding="utf-8") as fh:
                entries = [json.loads(line) for line in fh if line.strip()]
        if goal_id:
            entries = [e for e in entries if e.get("goal_id") == goal_id]
        return entries[-limit:] if limit else []
