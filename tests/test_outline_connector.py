import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from connector.outline import OutlineConnector


def _connector_with_transport(monkeypatch, handler):
    """Build a connector whose internal httpx.AsyncClient() calls are served
    by a MockTransport instead of hitting the network."""
    transport = httpx.MockTransport(handler)

    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("transport", transport)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _PatchedAsyncClient)
    return OutlineConnector(base_url="https://outline.example.com", api_key="key")


def make_raw_doc(**overrides):
    base = {
        "id": "doc-1",
        "title": "Test Doc",
        "text": "Hello world",
        "url": "/doc/test-doc",
        "tags": [{"name": "infra"}, {"name": "ssl"}],
        "collectionId": "col-1",
        "updatedAt": "2026-06-29T10:49:35.507Z",
    }
    base.update(overrides)
    return base


def test_parse_document_resolves_collection_name_from_cache():
    # Regression test: Outline's collectionId is an opaque UUID; without
    # resolving it via collections.list, search results can't show a
    # meaningful collection name.
    connector = OutlineConnector(base_url="https://outline.example.com", api_key="key")
    connector._collection_names = {"col-1": "project"}

    doc = connector._parse_document(make_raw_doc())

    assert doc.collection == "project"
    assert doc.doc_id == "doc-1"
    assert doc.tags == ["infra", "ssl"]
    assert doc.url == "https://outline.example.com/doc/test-doc"
    assert doc.updated_at == datetime(2026, 6, 29, 10, 49, 35, 507000, tzinfo=timezone.utc)


def test_parse_document_falls_back_to_raw_id_when_collection_unknown():
    connector = OutlineConnector(base_url="https://outline.example.com", api_key="key")

    doc = connector._parse_document(make_raw_doc(collectionId="unknown-col"))

    assert doc.collection == "unknown-col"


def test_parse_document_keeps_absolute_urls_untouched():
    connector = OutlineConnector(base_url="https://outline.example.com", api_key="key")

    doc = connector._parse_document(make_raw_doc(url="https://other.example.com/doc/x"))

    assert doc.url == "https://other.example.com/doc/x"


def test_parse_document_handles_missing_updated_at():
    connector = OutlineConnector(base_url="https://outline.example.com", api_key="key")

    doc = connector._parse_document(make_raw_doc(updatedAt=None))

    assert doc.updated_at is None


def test_public_url_defaults_to_base_url_when_not_set():
    connector = OutlineConnector(base_url="https://outline.example.com", api_key="key")

    assert connector.public_url == "https://outline.example.com"


def test_relative_doc_links_use_public_url_not_the_internal_api_url():
    # Regression test: when co-located on Outline's docker network,
    # base_url may be an internal-only hostname (http://outline:3000) used
    # for API calls. Doc URLs shown in search results must still use the
    # externally-clickable public_url, or every link becomes unreachable.
    connector = OutlineConnector(
        base_url="http://outline:3000",
        api_key="key",
        public_url="https://outline.example.com",
    )

    doc = connector._parse_document(make_raw_doc(url="/doc/test-doc"))

    assert doc.url == "https://outline.example.com/doc/test-doc"
    assert connector.base_url == "http://outline:3000"


def test_check_access_succeeds_on_200(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"data": {"id": "doc-1"}})

    connector = _connector_with_transport(monkeypatch, handler)

    asyncio.run(connector.check_access("doc-1"))  # must not raise


def test_check_access_raises_http_status_error_on_403(monkeypatch):
    def handler(request):
        return httpx.Response(403, json={"error": "forbidden"})

    connector = _connector_with_transport(monkeypatch, handler)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        asyncio.run(connector.check_access("doc-1"))
    assert exc_info.value.response.status_code == 403


def test_check_access_raises_http_status_error_on_401(monkeypatch):
    def handler(request):
        return httpx.Response(401, json={"error": "unauthorized"})

    connector = _connector_with_transport(monkeypatch, handler)

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        asyncio.run(connector.check_access("doc-1"))
    assert exc_info.value.response.status_code == 401
