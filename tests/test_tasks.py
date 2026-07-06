from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import indexer.tasks as tasks_module


def make_doc(doc_id, updated_at, title="Title"):
    return SimpleNamespace(doc_id=doc_id, title=title, updated_at=updated_at, text="body")


class FakeConnector:
    def __init__(self, docs=(), get_document_result=None):
        self._docs = list(docs)
        self._get_document_result = get_document_result

    async def iter_all_documents(self):
        for doc in self._docs:
            yield doc

    async def get_document(self, doc_id):
        return self._get_document_result


@pytest.fixture(autouse=True)
def no_redis_locks(monkeypatch):
    # These tasks are unit-tested in isolation from Redis; locking behaviour
    # itself is covered by tests/test_sync_lock.py.
    monkeypatch.setattr(tasks_module, "acquire_sync_lock", lambda: True)
    monkeypatch.setattr(tasks_module, "release_sync_lock", lambda: None)
    monkeypatch.setattr(tasks_module, "doc_lock", lambda doc_id: nullcontext())


def test_run_sync_skips_documents_not_changed_since_cursor(monkeypatch):
    now = datetime.now(timezone.utc)
    old_doc = make_doc("old-doc", now - timedelta(days=2))
    new_doc = make_doc("new-doc", now)

    monkeypatch.setattr(tasks_module, "get_last_synced_at", lambda: now - timedelta(days=1))
    set_calls = []
    monkeypatch.setattr(tasks_module, "set_last_synced_at", lambda dt: set_calls.append(dt))
    monkeypatch.setattr(tasks_module, "get_all_doc_ids", lambda: {"old-doc", "new-doc"})
    monkeypatch.setattr(tasks_module, "_connector", lambda: FakeConnector([old_doc, new_doc]))

    indexed = []
    monkeypatch.setattr(tasks_module, "index_document", lambda doc: indexed.append(doc.doc_id))
    deleted = []
    monkeypatch.setattr(tasks_module, "delete_document", lambda doc_id: deleted.append(doc_id))

    tasks_module.run_sync(full=False)

    assert indexed == ["new-doc"]  # old-doc unchanged since cursor -> skipped
    assert deleted == []
    assert len(set_calls) == 1  # cursor advanced after a successful run


def test_run_sync_removes_documents_no_longer_in_outline(monkeypatch):
    now = datetime.now(timezone.utc)
    live_doc = make_doc("live-doc", now)

    monkeypatch.setattr(tasks_module, "get_last_synced_at", lambda: None)
    monkeypatch.setattr(tasks_module, "set_last_synced_at", lambda dt: None)
    monkeypatch.setattr(tasks_module, "get_all_doc_ids", lambda: {"live-doc", "ghost-doc"})
    monkeypatch.setattr(tasks_module, "_connector", lambda: FakeConnector([live_doc]))
    monkeypatch.setattr(tasks_module, "index_document", lambda doc: None)
    deleted = []
    monkeypatch.setattr(tasks_module, "delete_document", lambda doc_id: deleted.append(doc_id))

    tasks_module.run_sync(full=False)

    assert deleted == ["ghost-doc"]  # gone from Outline -> pruned from Qdrant


def test_run_sync_forces_full_reembed_when_full_true(monkeypatch):
    now = datetime.now(timezone.utc)
    old_doc = make_doc("old-doc", now - timedelta(days=30))

    # Even though a recent cursor exists, full=True must ignore it.
    monkeypatch.setattr(tasks_module, "get_last_synced_at", lambda: now - timedelta(minutes=1))
    monkeypatch.setattr(tasks_module, "set_last_synced_at", lambda dt: None)
    monkeypatch.setattr(tasks_module, "get_all_doc_ids", lambda: {"old-doc"})
    monkeypatch.setattr(tasks_module, "_connector", lambda: FakeConnector([old_doc]))
    indexed = []
    monkeypatch.setattr(tasks_module, "index_document", lambda doc: indexed.append(doc.doc_id))
    monkeypatch.setattr(tasks_module, "delete_document", lambda doc_id: None)

    tasks_module.run_sync(full=True)

    assert indexed == ["old-doc"]


def test_run_sync_skips_entirely_when_lock_not_acquired(monkeypatch):
    monkeypatch.setattr(tasks_module, "acquire_sync_lock", lambda: False)
    connector_created = []
    monkeypatch.setattr(
        tasks_module, "_connector", lambda: connector_created.append("should not run")
    )

    tasks_module.run_sync(full=False)

    assert connector_created == []


def test_process_webhook_event_indexes_on_update_event(monkeypatch):
    doc = make_doc("doc-1", datetime.now(timezone.utc))
    monkeypatch.setattr(tasks_module, "_connector", lambda: FakeConnector(get_document_result=doc))
    indexed = []
    monkeypatch.setattr(tasks_module, "index_document", lambda d: indexed.append(d.doc_id))

    tasks_module.process_webhook_event(event="documents.update", doc_id="doc-1")

    assert indexed == ["doc-1"]


def test_process_webhook_event_deletes_on_delete_event(monkeypatch):
    deleted = []
    monkeypatch.setattr(tasks_module, "delete_document", lambda doc_id: deleted.append(doc_id))

    tasks_module.process_webhook_event(event="documents.delete", doc_id="doc-1")

    assert deleted == ["doc-1"]


def test_process_webhook_event_ignores_unhandled_events(monkeypatch):
    indexed = []
    deleted = []
    monkeypatch.setattr(tasks_module, "index_document", lambda d: indexed.append(d))
    monkeypatch.setattr(tasks_module, "delete_document", lambda i: deleted.append(i))

    tasks_module.process_webhook_event(event="documents.viewed", doc_id="doc-1")

    assert indexed == []
    assert deleted == []
