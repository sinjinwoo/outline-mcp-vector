import asyncio

import httpx
import pytest

import mcpserver.main as mcp_main


def _candidate(doc_id: str, score: float) -> dict:
    return {
        "score": score,
        "title": doc_id,
        "url": f"https://outline.example.com/doc/{doc_id}",
        "source": "outline",
        "collection": "",
        "tags": [],
        "snippet": "",
        "doc_id": doc_id,
    }


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://outline.example.com/api/documents.info")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


@pytest.fixture(autouse=True)
def _stub_embedder(monkeypatch):
    monkeypatch.setattr(mcp_main, "embed_query", lambda query: [0.0])


def test_search_knowledge_returns_all_when_everything_passes(monkeypatch):
    first_batch = [_candidate("doc-1", 0.9), _candidate("doc-2", 0.8), _candidate("doc-3", 0.7)]

    def fake_search(embedding, limit, offset=0):
        assert offset == 0
        return first_batch[:limit]

    async def fake_check_access(doc_id):
        return None  # success

    monkeypatch.setattr(mcp_main, "search", fake_search)
    monkeypatch.setattr(mcp_main._outline, "check_access", fake_check_access)

    result = asyncio.run(mcp_main.search_knowledge(query="x", limit=3))

    assert [c["doc_id"] for c in result] == ["doc-1", "doc-2", "doc-3"]


def test_search_knowledge_backfills_when_some_denied(monkeypatch):
    first_batch = [_candidate("keep-1", 0.9), _candidate("deny-1", 0.8)]
    extra_batch = [_candidate(f"extra-{i}", 0.7 - i * 0.01) for i in range(38)]

    def fake_search(embedding, limit, offset=0):
        if offset == 0:
            return first_batch[:limit]
        assert offset == 2
        return extra_batch[:limit]

    async def fake_check_access(doc_id):
        if doc_id == "deny-1":
            raise _http_error(403)
        return None

    monkeypatch.setattr(mcp_main, "search", fake_search)
    monkeypatch.setattr(mcp_main._outline, "check_access", fake_check_access)

    result = asyncio.run(mcp_main.search_knowledge(query="x", limit=2))

    assert len(result) == 2
    assert [c["doc_id"] for c in result] == ["keep-1", "extra-0"]


def test_search_knowledge_cancels_remaining_once_enough_verified(monkeypatch):
    first_batch = [_candidate("keep-1", 0.9), _candidate("deny-1", 0.8)]
    extra_batch = [_candidate(f"extra-{i}", 0.7 - i * 0.01) for i in range(10)]
    completed: list[str] = []

    def fake_search(embedding, limit, offset=0):
        if offset == 0:
            return first_batch[:limit]
        return extra_batch[:limit]

    async def fake_check_access(doc_id):
        if doc_id == "deny-1":
            raise _http_error(403)
        if doc_id.startswith("extra-"):
            idx = int(doc_id.split("-")[1])
            # Later-ranked candidates resolve later, giving the earlier ones
            # a chance to satisfy `needed` and trigger cancellation first.
            await asyncio.sleep(idx * 0.02)
        completed.append(doc_id)
        return None

    monkeypatch.setattr(mcp_main, "search", fake_search)
    monkeypatch.setattr(mcp_main._outline, "check_access", fake_check_access)

    result = asyncio.run(mcp_main.search_knowledge(query="x", limit=2))

    assert [c["doc_id"] for c in result] == ["keep-1", "extra-0"]
    # The slowest backfill candidates should have been cancelled before their
    # sleep finished, once extra-0 alone satisfied the shortfall.
    assert "extra-9" not in completed


def test_search_knowledge_returns_fewer_than_limit_when_cap_exhausted(monkeypatch):
    first_batch = [_candidate(f"deny-{i}", 0.9 - i * 0.01) for i in range(3)]
    extra_batch = [_candidate(f"deny-extra-{i}", 0.5 - i * 0.01) for i in range(37)]

    def fake_search(embedding, limit, offset=0):
        if offset == 0:
            return first_batch[:limit]
        assert offset == 3
        return extra_batch[:limit]

    async def fake_check_access(doc_id):
        raise _http_error(404)

    monkeypatch.setattr(mcp_main, "search", fake_search)
    monkeypatch.setattr(mcp_main._outline, "check_access", fake_check_access)

    result = asyncio.run(mcp_main.search_knowledge(query="x", limit=3))

    assert result == []


def test_search_knowledge_raises_on_401_without_backfilling(monkeypatch):
    first_batch = [_candidate("doc-1", 0.9), _candidate("doc-2", 0.8)]
    search_calls = []

    def fake_search(embedding, limit, offset=0):
        search_calls.append(offset)
        return first_batch[:limit]

    async def fake_check_access(doc_id):
        if doc_id == "doc-2":
            raise _http_error(401)
        return None

    monkeypatch.setattr(mcp_main, "search", fake_search)
    monkeypatch.setattr(mcp_main._outline, "check_access", fake_check_access)

    with pytest.raises(RuntimeError):
        asyncio.run(mcp_main.search_knowledge(query="x", limit=2))

    # Only the first (limit-sized) batch should have been fetched — the 401
    # must short-circuit before any backfill round is attempted.
    assert search_calls == [0]
