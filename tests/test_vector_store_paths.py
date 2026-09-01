from app.vector_store import FaissStore


def test_custom_vector_store_paths(tmp_path):
    index_path = tmp_path / "evaluation.index"
    metadata_path = tmp_path / "evaluation_metadata.json"

    store = FaissStore(
        index_path=index_path,
        metadata_path=metadata_path,
    )

    assert store.index_path == index_path
    assert store.metadata_path == metadata_path
    assert store.index is None
    assert store.metadata == []


def test_custom_vector_store_does_not_use_default_index(tmp_path):
    store = FaissStore(
        index_path=tmp_path / "isolated.index",
        metadata_path=tmp_path / "isolated.json",
    )

    assert str(store.index_path) != "data/faiss.index"
    assert str(store.metadata_path) != "data/metadata.json"
