from datetime import datetime, timezone

from connector.outline import OutlineConnector


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
