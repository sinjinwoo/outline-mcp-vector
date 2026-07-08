from types import SimpleNamespace

import shared.vector_store as vector_store


class FakeQdrantClient:
    def __init__(self, hits):
        self._hits = hits

    def search(self, **kwargs):
        return self._hits


def test_search_returns_full_chunk_text_without_truncation(monkeypatch):
    # Regression test: search() used to hard-truncate the snippet to 500
    # chars regardless of how the underlying chunk was actually sized by
    # chunk_markdown() (which already caps chunks at ~3200 chars).
    long_text = "word " * 700  # comfortably over the old 500-char cutoff
    hit = SimpleNamespace(
        score=0.9,
        payload={
            "chunk_text": long_text,
            "doc_id": "doc-1",
            "title": "Title",
            "url": "https://outline.example.com/doc/1",
            "source": "outline",
            "collection": "project",
            "tags": [],
        },
    )
    monkeypatch.setattr(vector_store, "get_client", lambda: FakeQdrantClient([hit]))

    results = vector_store.search([0.0], limit=1)

    assert results[0]["snippet"] == long_text
    assert len(results[0]["snippet"]) > 500
