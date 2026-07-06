"""Redis-backed lock so only one sync runs at a time across Celery workers.

A single in-process flag (as used before Celery) doesn't work once sync
runs in a separate worker process from the FastAPI API process — both need
a shared view of "is a sync currently running".
"""

import os
from contextlib import contextmanager

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/1")
_LOCK_KEY = "outline_rag:sync_running"
_LOCK_TTL_SECONDS = 30 * 60  # safety net in case a worker dies mid-sync

_DOC_LOCK_TIMEOUT_SECONDS = 5 * 60  # max time a single doc lock is held
_DOC_LOCK_BLOCKING_TIMEOUT_SECONDS = 60  # how long a caller waits for the lock

_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(REDIS_URL)
    return _client


def acquire_sync_lock() -> bool:
    return bool(_get_client().set(_LOCK_KEY, "1", nx=True, ex=_LOCK_TTL_SECONDS))


def release_sync_lock() -> None:
    _get_client().delete(_LOCK_KEY)


def is_sync_running() -> bool:
    return _get_client().exists(_LOCK_KEY) == 1


@contextmanager
def doc_lock(doc_id: str):
    """Serialize every index/delete call for a single document.

    A webhook event and a periodic/startup sync pass can both decide to
    (re)index the same document at the same time (e.g. the hourly sync is
    mid-embedding a document just as its webhook edit event arrives).
    Without this, their delete+upsert calls can interleave and leave Qdrant
    with a stale version of the document. Every write path for a given
    doc_id must go through this lock.
    """
    lock = _get_client().lock(
        f"outline_rag:doc_lock:{doc_id}",
        timeout=_DOC_LOCK_TIMEOUT_SECONDS,
        blocking_timeout=_DOC_LOCK_BLOCKING_TIMEOUT_SECONDS,
    )
    if not lock.acquire():
        raise TimeoutError(f"Timed out waiting for the index lock on document {doc_id}")
    try:
        yield
    finally:
        lock.release()
