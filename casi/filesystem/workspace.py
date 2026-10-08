"""Jailed workspace filesystem.

:class:`Workspace` confines every path under a root directory: absolute
paths and any ``..`` segment raise :class:`SecurityError`, and the resolved
path is additionally verified to stay inside the root (symlink-safe).

.. note::
    ``SecurityError`` is defined here in ``workspace.py`` rather than imported
    from ``casi.execution.sandbox`` — a deliberate choice to avoid a
    cross-package import from the filesystem layer into the execution layer.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_TOKEN_RE = re.compile(r"\w+")


class SecurityError(Exception):
    """Raised when a path escapes the workspace root (jail violation)."""


@dataclass
class FileMeta:
    """Metadata for a file in the workspace."""

    name: str  # workspace-relative POSIX path
    size: int  # bytes
    mtime: str  # ISO-8601 timestamp
    version: int  # latest version written via Workspace.write (0 if never)


@dataclass
class FileVersion:
    """One versioned snapshot of a file's content."""

    version: int
    content: str
    author: str
    created_at: str  # ISO-8601 UTC


class Workspace:
    """File storage rooted at ``root``, with version history and keyword search.

    The last 10 versions of each file are kept in memory; the file itself is
    written to disk on every :meth:`write`.
    """

    _MAX_VERSIONS = 10

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._versions: dict[str, list[FileVersion]] = {}

    @property
    def root(self) -> Path:
        """The workspace root directory (read-only)."""
        return self._root

    @staticmethod
    def _key(relpath: str | Path) -> str:
        """Canonical in-memory key for a workspace-relative path."""
        return Path(relpath).as_posix()

    def _resolve(self, relpath: str) -> Path:
        """Resolve ``relpath`` inside the root; jail violations -> SecurityError."""
        candidate = Path(relpath)
        if candidate.is_absolute():
            raise SecurityError(f"absolute paths are not allowed: {relpath!r}")
        if ".." in candidate.parts:
            raise SecurityError(f"parent-directory segments are not allowed: {relpath!r}")
        resolved = (self._root / candidate).resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError:
            raise SecurityError(f"path escapes workspace root: {relpath!r}")
        return resolved

    def write(self, relpath: str, content: str, author: str = "") -> FileVersion:
        """Write ``content`` to ``relpath`` (creating parents), versioned.

        Keeps the last 10 versions in memory. Returns the new :class:`FileVersion`.
        """
        target = self._resolve(relpath)
        target.parent.mkdir(parents=True, exist_ok=True)
        history = self._versions.setdefault(self._key(relpath), [])
        version = history[-1].version + 1 if history else 1
        snapshot = FileVersion(
            version=version,
            content=content,
            author=author,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        history.append(snapshot)
        del history[: max(0, len(history) - self._MAX_VERSIONS)]
        target.write_text(content, encoding="utf-8")
        return snapshot

    def read(self, relpath: str) -> str:
        """Return the text content of ``relpath``.

        Raises:
            SecurityError: If ``relpath`` escapes the root.
            FileNotFoundError: If the file does not exist.
        """
        return self._resolve(relpath).read_text(encoding="utf-8")

    def exists(self, relpath: str) -> bool:
        """Return True if ``relpath`` exists as a file in the workspace."""
        try:
            return self._resolve(relpath).is_file()
        except SecurityError:
            return False

    def list(self, prefix: str = "") -> list[FileMeta]:
        """List files under the root (optionally filtered by ``prefix``).

        Returns :class:`FileMeta` entries sorted by name for determinism.
        """
        metas: list[FileMeta] = []
        for path in sorted(self._root.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(self._root).as_posix()
            if prefix and not rel.startswith(prefix):
                continue
            stat = path.stat()
            version = self._versions.get(rel, [None])[-1]
            metas.append(
                FileMeta(
                    name=rel,
                    size=stat.st_size,
                    mtime=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    version=version.version if version else 0,
                )
            )
        return metas

    def history(self, relpath: str) -> list[FileVersion]:
        """Return the in-memory version history for ``relpath`` (oldest first)."""
        return list(self._versions.get(self._key(relpath), []))

    def search(self, query: str, k: int = 10) -> list[FileMeta]:
        """Keyword search over file contents.

        Scores each file by the count of query-token hits (lowercase
        ``\\w+`` tokens, min length 2) in its text. Results are ordered by
        descending score, then by name for determinism; files with no hits
        are excluded.
        """
        query_tokens = [t for t in _TOKEN_RE.findall(query.lower()) if len(t) >= 2]
        if not query_tokens:
            return []
        scored: list[tuple[int, FileMeta]] = []
        for meta in self.list():
            try:
                text = self.read(meta.name)
            except (FileNotFoundError, SecurityError, UnicodeDecodeError):
                continue
            counts = Counter(_TOKEN_RE.findall(text.lower()))
            score = sum(counts.get(tok, 0) for tok in query_tokens)
            if score > 0:
                scored.append((score, meta))
        scored.sort(key=lambda item: (-item[0], item[1].name))
        return [meta for _, meta in scored[:k]]
