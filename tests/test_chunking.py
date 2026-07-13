from app.vector_store import chunk_text


def test_chunk_text_has_overlap_and_preserves_content() -> None:
    text = "a" * 2000
    chunks = chunk_text(text, chunk_size=500, overlap=50)
    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)


def test_chunk_text_empty() -> None:
    assert chunk_text("   ") == []
