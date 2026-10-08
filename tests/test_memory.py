"""Tests for casi.memory: WorkingMemory, LongTermMemory, MemorySystem."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from casi.memory import LongTermMemory, MemorySystem, WorkingMemory

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_working_memory_set_get() -> None:
    mem = WorkingMemory()
    mem.set("goal-1", "plan", {"steps": 3})
    assert mem.get("goal-1", "plan") == {"steps": 3}
    assert mem.get("goal-1", "missing") is None
    assert mem.get("goal-1", "missing", default="dflt") == "dflt"


def test_working_memory_isolation_per_goal() -> None:
    mem = WorkingMemory()
    mem.set("goal-1", "key", "value-1")
    mem.set("goal-2", "key", "value-2")
    assert mem.get("goal-1", "key") == "value-1"
    assert mem.get("goal-2", "key") == "value-2"
    assert mem.get_all("goal-1") == {"key": "value-1"}
    # get_all returns a copy: mutating it must not affect the store.
    snapshot = mem.get_all("goal-1")
    snapshot["key"] = "mutated"
    assert mem.get("goal-1", "key") == "value-1"


def test_working_memory_clear() -> None:
    mem = WorkingMemory()
    mem.set("goal-1", "a", 1)
    mem.set("goal-2", "b", 2)
    mem.clear("goal-1")
    assert mem.get_all("goal-1") == {}
    assert mem.get("goal-2", "b") == 2  # other goals untouched
    mem.clear("goal-1")  # clearing twice is fine


DOCS = [
    "Quicksort is a divide and conquer sorting algorithm for arrays",
    "Baking sourdough bread requires flour water salt and patience",
    "Neural networks learn representations through gradient descent training",
]


def _remember_three(path) -> LongTermMemory:
    mem = LongTermMemory(path)
    for doc in DOCS:
        mem.remember(doc)
    return mem


def test_longterm_recall_ranking(tmp_path) -> None:
    mem = _remember_three(tmp_path / "lt.jsonl")
    top = mem.recall("quicksort sorting algorithm", k=3)
    assert top[0]["text"] == DOCS[0]
    assert top[0]["score"] > 0
    top2 = mem.recall("gradient descent neural networks", k=3)
    assert top2[0]["text"] == DOCS[2]
    top3 = mem.recall("sourdough bread baking", k=3)
    assert top3[0]["text"] == DOCS[1]
    for entry in top:
        assert set(entry) == {"id", "text", "metadata", "score"}


def test_longterm_recall_deterministic_tie_break(tmp_path) -> None:
    mem = LongTermMemory(tmp_path / "lt.jsonl")
    mem.remember("identical document text here")
    mem.remember("identical document text here")
    first = [r["id"] for r in mem.recall("identical document", k=5)]
    second = [r["id"] for r in mem.recall("identical document", k=5)]
    assert first == second
    assert first == sorted(first)  # ties broken by id


def test_longterm_persistence_across_instances(tmp_path) -> None:
    path = tmp_path / "lt.jsonl"
    assert not path.exists()  # file must not exist before first remember
    mem = LongTermMemory(path)
    entry_id = mem.remember("persistent memory entry", metadata={"tag": "x"})
    assert path.exists()
    mem2 = LongTermMemory(path)  # new instance reads the same file
    results = mem2.recall("persistent memory", k=5)
    assert results[0]["id"] == entry_id
    assert results[0]["metadata"] == {"tag": "x"}


def test_longterm_empty_query_returns_empty(tmp_path) -> None:
    mem = _remember_three(tmp_path / "lt.jsonl")
    assert mem.recall("") == []
    assert mem.recall("   ") == []


def test_longterm_empty_store_returns_empty(tmp_path) -> None:
    mem = LongTermMemory(tmp_path / "lt.jsonl")
    assert mem.recall("anything at all") == []


def test_longterm_remember_empty_raises(tmp_path) -> None:
    mem = LongTermMemory(tmp_path / "lt.jsonl")
    with pytest.raises(ValueError):
        mem.remember("")
    with pytest.raises(ValueError):
        mem.remember("   ")


_CROSS_PROCESS_CHILD = """
import sys
from casi.memory import MemorySystem

mode, datadir = sys.argv[1], sys.argv[2]
ms = MemorySystem(datadir)
if mode == "write":
    ms.longterm.remember(
        "the lighthouse keeper logs the tides nightly", {"source": "run-1"}
    )
    print("wrote", flush=True)
else:
    hits = ms.longterm.recall("lighthouse keeper tides")
    print(hits[0]["text"] if hits else "NONE", flush=True)
    print(hits[0]["metadata"] if hits else "NONE", flush=True)
"""


def test_longterm_persistence_across_processes(tmp_path) -> None:
    """Write long-term memory in one OS process, recall it from a *new*
    MemorySystem instance in a *different* OS process. Proves the JSONL
    backing is the persistence mechanism (no shared file handles)."""
    env = dict(os.environ, PYTHONPATH=str(_PROJECT_ROOT))
    data_dir = tmp_path / "data"

    run1 = subprocess.run(
        [sys.executable, "-c", _CROSS_PROCESS_CHILD, "write", str(data_dir)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert run1.returncode == 0, run1.stderr
    assert "wrote" in run1.stdout

    run2 = subprocess.run(
        [sys.executable, "-c", _CROSS_PROCESS_CHILD, "read", str(data_dir)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert run2.returncode == 0, run2.stderr
    lines = run2.stdout.splitlines()
    assert "the lighthouse keeper logs the tides nightly" in lines[0]
    assert "run-1" in lines[1]


def test_memory_system_wiring(tmp_path) -> None:
    system = MemorySystem(tmp_path / "data")
    assert isinstance(system.working, WorkingMemory)
    assert isinstance(system.longterm, LongTermMemory)
    assert system.longterm.path == tmp_path / "data" / "longterm.jsonl"
    system.working.set("g", "k", "v")
    assert system.working.get("g", "k") == "v"
    system.longterm.remember("hello world")
    assert system.longterm.recall("hello")[0]["text"] == "hello world"
