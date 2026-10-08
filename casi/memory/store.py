"""CASI memory subsystem.

* :class:`WorkingMemory` — in-memory, per-goal short-term key/value storage.
* :class:`LongTermMemory` — persistent JSONL store with genuine TF-IDF cosine
  recall (tokenization: lowercase, ``\\w+`` regex, minimum token length 2;
  IDF computed over the stored corpus). No embeddings, no network.
* :class:`MemorySystem` — composition root wiring the two together.
"""

from __future__ import annotations

import json
import math
import re
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_TOKEN_RE = re.compile(r"\w+")


def _tokenize(text: str) -> list[str]:
    """Lowercase ``\\w+`` tokenization, dropping tokens shorter than 2 chars."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= 2]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkingMemory:
    """In-memory per-goal short-term memory: ``{goal_id: {key: value}}``."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def set(self, goal_id: str, key: str, value: Any) -> None:
        """Store ``value`` under ``key`` for ``goal_id``."""
        self._data.setdefault(goal_id, {})[key] = value

    def get(self, goal_id: str, key: str, default: Any = None) -> Any:
        """Return the value for ``key`` under ``goal_id``, or ``default``."""
        return self._data.get(goal_id, {}).get(key, default)

    def get_all(self, goal_id: str) -> dict[str, Any]:
        """Return a copy of all key/value pairs stored for ``goal_id``."""
        return dict(self._data.get(goal_id, {}))

    def clear(self, goal_id: str) -> None:
        """Drop all entries stored for ``goal_id`` (other goals untouched)."""
        self._data.pop(goal_id, None)


class LongTermMemory:
    """Persistent long-term memory backed by a JSONL file.

    The file is created on the first :meth:`remember` call (not in
    ``__init__``). All mutations are guarded by a lock so instances are
    safe to share across threads. One JSON object per line::

        {"id": ..., "text": ..., "metadata": {...}, "created_at": ...}
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._entries: list[dict[str, Any]] = []
        if self._path.exists():
            with self._path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        self._entries.append(json.loads(line))

    @property
    def path(self) -> Path:
        """Filesystem path of the backing JSONL store."""
        return self._path

    def remember(self, text: str, metadata: dict[str, Any] | None = None) -> str:
        """Persist ``text`` with optional ``metadata``; return the entry id.

        The id is the first 8 hex chars of a uuid4.

        Raises:
            ValueError: If ``text`` is empty or blank.
        """
        if not text or not text.strip():
            raise ValueError("text must be a non-empty string")
        entry = {
            "id": uuid.uuid4().hex[:8],
            "text": text,
            "metadata": dict(metadata) if metadata else {},
            "created_at": _utcnow_iso(),
        }
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
            self._entries.append(entry)
        return entry["id"]

    def recall(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        """Return up to ``k`` entries ranked by TF-IDF cosine similarity.

        Each result is ``{"id", "text", "metadata", "score"}``. Ties are
        broken deterministically by entry id. An empty query or an empty
        store returns ``[]``.
        """
        query_tokens = _tokenize(query)
        with self._lock:
            docs = list(self._entries)
        if not query_tokens or not docs:
            return []

        n_docs = len(docs)
        doc_counts: list[Counter] = []
        doc_freq: dict[str, int] = {}
        for entry in docs:
            counts = Counter(_tokenize(entry["text"]))
            doc_counts.append(counts)
            for term in counts:
                doc_freq[term] = doc_freq.get(term, 0) + 1

        def idf(term: str) -> float:
            # Smoothed IDF so unseen query terms still get a finite weight.
            return math.log((n_docs + 1) / (doc_freq.get(term, 0) + 1)) + 1.0

        query_counts = Counter(query_tokens)
        query_len = len(query_tokens)
        query_vec = {t: (c / query_len) * idf(t) for t, c in query_counts.items()}
        query_norm = math.sqrt(sum(w * w for w in query_vec.values()))

        scored: list[dict[str, Any]] = []
        for entry, counts in zip(docs, doc_counts):
            doc_len = sum(counts.values())
            dot = 0.0
            doc_norm_sq = 0.0
            for term, count in counts.items():
                weight = (count / doc_len) * idf(term)
                doc_norm_sq += weight * weight
                if term in query_vec:
                    dot += weight * query_vec[term]
            doc_norm = math.sqrt(doc_norm_sq)
            score = dot / (query_norm * doc_norm) if query_norm and doc_norm else 0.0
            scored.append(
                {
                    "id": entry["id"],
                    "text": entry["text"],
                    "metadata": entry["metadata"],
                    "score": score,
                }
            )
        scored.sort(key=lambda r: (-r["score"], r["id"]))
        return scored[:k]


class MemorySystem:
    """Composition root: a :class:`WorkingMemory` plus a file-backed :class:`LongTermMemory`."""

    def __init__(self, data_dir: str | Path) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._working = WorkingMemory()
        self._longterm = LongTermMemory(self._data_dir / "longterm.jsonl")

    @property
    def working(self) -> WorkingMemory:
        """The in-memory per-goal working memory."""
        return self._working

    @property
    def longterm(self) -> LongTermMemory:
        """The persistent long-term memory (backed by ``data_dir/longterm.jsonl``)."""
        return self._longterm
