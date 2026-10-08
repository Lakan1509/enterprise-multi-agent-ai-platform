"""Tests for casi.filesystem.workspace."""

import pytest

from casi.filesystem import SecurityError, Workspace


def test_write_read_roundtrip(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    version = ws.write("docs/hello.txt", "hello world", author="tester")
    assert version.version == 1
    assert version.author == "tester"
    assert ws.read("docs/hello.txt") == "hello world"
    assert ws.exists("docs/hello.txt")
    assert not ws.exists("docs/missing.txt")


def test_read_missing_raises_file_not_found(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    with pytest.raises(FileNotFoundError):
        ws.read("nope.txt")


def test_history_versions(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write("f.txt", "v1")
    ws.write("f.txt", "v2", author="bob")
    history = ws.history("f.txt")
    assert [h.version for h in history] == [1, 2]
    assert history[-1].content == "v2"
    assert history[-1].author == "bob"
    assert ws.history("unknown.txt") == []


def test_version_history_capped_at_ten(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    for i in range(12):
        ws.write("f.txt", f"content-{i}")
    history = ws.history("f.txt")
    assert len(history) == 10
    assert history[0].version == 3  # oldest two evicted
    assert history[-1].version == 12
    assert history[-1].content == "content-11"


@pytest.mark.parametrize("bad", ["../x.txt", "/abs.txt", "a/../../x.txt", ".."])
def test_path_escape_rejected(tmp_path, bad: str) -> None:
    ws = Workspace(tmp_path / "ws")
    with pytest.raises(SecurityError):
        ws.write(bad, "evil")
    with pytest.raises(SecurityError):
        ws.read(bad)
    assert not ws.exists(bad)


def test_list_and_prefix(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write("a/one.txt", "1")
    ws.write("a/two.txt", "22")
    ws.write("b/three.txt", "333")
    metas = ws.list()
    assert [m.name for m in metas] == ["a/one.txt", "a/two.txt", "b/three.txt"]
    assert [m.name for m in ws.list(prefix="a/")] == ["a/one.txt", "a/two.txt"]
    first = metas[0]
    assert first.size == 1
    assert first.mtime
    assert first.version == 1


def test_search_ranking(tmp_path) -> None:
    ws = Workspace(tmp_path / "ws")
    ws.write("low.txt", "python is great")
    ws.write("high.txt", "python python python rocks")
    ws.write("other.txt", "nothing relevant here")
    results = ws.search("python")
    assert [m.name for m in results] == ["high.txt", "low.txt"]
    assert ws.search("zzzznothing") == []
    assert ws.search("") == []
