import threading
import time

import fakeredis
import pytest

import indexer.sync_lock as sync_lock


@pytest.fixture(autouse=True)
def fake_redis_client(monkeypatch):
    fake = fakeredis.FakeRedis()
    monkeypatch.setattr(sync_lock, "_client", fake)
    yield fake


def test_acquire_sync_lock_prevents_second_acquire():
    assert sync_lock.acquire_sync_lock() is True
    assert sync_lock.acquire_sync_lock() is False

    sync_lock.release_sync_lock()

    assert sync_lock.is_sync_running() is False
    assert sync_lock.acquire_sync_lock() is True


def test_doc_lock_serializes_access_to_the_same_document():
    # Regression test: a webhook event and a sync pass processing the same
    # document at the same time must not interleave their index/delete
    # calls, or the loser's write can clobber the winner's.
    events = []

    def hold_lock(name, hold_seconds):
        with sync_lock.doc_lock("doc-123"):
            events.append(f"{name}-start")
            time.sleep(hold_seconds)
            events.append(f"{name}-end")

    first = threading.Thread(target=hold_lock, args=("A", 0.3))
    second = threading.Thread(target=hold_lock, args=("B", 0.0))

    first.start()
    time.sleep(0.05)  # ensure A grabs the lock first
    second.start()
    first.join()
    second.join()

    assert events == ["A-start", "A-end", "B-start", "B-end"]


def test_doc_lock_does_not_block_unrelated_documents():
    order = []

    def hold(doc_id, hold_seconds):
        with sync_lock.doc_lock(doc_id):
            order.append(f"{doc_id}-start")
            time.sleep(hold_seconds)
            order.append(f"{doc_id}-end")

    first = threading.Thread(target=hold, args=("doc-A", 0.2))
    second = threading.Thread(target=hold, args=("doc-B", 0.2))
    first.start()
    second.start()
    first.join()
    second.join()

    # Both must be able to start before either finishes (no cross-blocking).
    assert order.index("doc-A-start") < order.index("doc-B-end")
    assert order.index("doc-B-start") < order.index("doc-A-end")
