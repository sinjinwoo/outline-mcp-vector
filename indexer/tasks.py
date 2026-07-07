"""Celery tasks: the actual work behind webhook events and sync runs.

Kept separate from main.py so the FastAPI process only ever enqueues work —
all Outline API calls, chunking, embedding, and Qdrant writes happen in the
Celery worker process and survive an API-process restart.
"""

import asyncio
import os
from datetime import datetime, timezone

from connector.outline import OutlineConnector
from indexer.celery_app import celery_app
from indexer.pipeline import delete_document, index_document
from indexer.sync_lock import acquire_sync_lock, doc_lock, release_sync_lock
from shared.sync_state import get_last_synced_at, set_last_synced_at
from shared.vector_store import get_all_doc_ids

# OUTLINE_API_URL: used for actual API calls — set to the internal Docker
# hostname (e.g. http://outline:3000) when co-located on Outline's network
# to skip the public round-trip. Falls back to OUTLINE_BASE_URL when unset.
OUTLINE_API_URL = os.getenv("OUTLINE_API_URL") or os.getenv("OUTLINE_BASE_URL", "")
# OUTLINE_PUBLIC_URL: used to build the doc URLs shown in search results,
# which must stay externally clickable regardless of OUTLINE_API_URL.
OUTLINE_PUBLIC_URL = os.getenv("OUTLINE_PUBLIC_URL") or os.getenv("OUTLINE_BASE_URL", "")
OUTLINE_API_KEY = os.getenv("OUTLINE_API_KEY", "")

INDEX_EVENTS = {"documents.create", "documents.update", "documents.publish"}
DELETE_EVENTS = {"documents.delete", "documents.archive"}


def _connector() -> OutlineConnector:
    return OutlineConnector(
        base_url=OUTLINE_API_URL,
        api_key=OUTLINE_API_KEY,
        public_url=OUTLINE_PUBLIC_URL,
    )


@celery_app.task(name="indexer.process_webhook_event", max_retries=3, default_retry_delay=30)
def process_webhook_event(event: str, doc_id: str) -> None:
    """Handle a single Outline webhook event: re-fetch and re-index, or delete."""
    try:
        if event in INDEX_EVENTS:
            doc = asyncio.run(_connector().get_document(doc_id))
            with doc_lock(doc_id):
                index_document(doc)
        elif event in DELETE_EVENTS:
            with doc_lock(doc_id):
                delete_document(doc_id)
        else:
            print(f"[worker] Ignored event: {event}")
    except Exception as exc:
        print(f"[worker] Error processing {event} for {doc_id}: {exc}")
        raise process_webhook_event.retry(exc=exc)


@celery_app.task(name="indexer.run_sync")
def run_sync(full: bool = False) -> None:
    """Sync Outline -> Qdrant.

    Only documents whose updatedAt is newer than the last successful sync
    are re-embedded (no full re-embedding on every run). Documents that no
    longer exist in Outline (deleted/archived) are removed from Qdrant.
    Pass full=True to ignore the cursor and re-embed every document.
    """
    if not acquire_sync_lock():
        print("[worker] Sync already running — skipping")
        return

    sync_started_at = datetime.now(timezone.utc)
    since = None if full else get_last_synced_at()
    counters = {"indexed": 0, "skipped": 0, "failed": 0, "removed": 0}

    try:
        if since is None:
            print("[worker] Running full sync (no prior cursor / forced)")
        else:
            print(f"[worker] Running incremental sync since {since.isoformat()}")

        connector = _connector()

        async def _walk_documents() -> set[str]:
            live_ids: set[str] = set()
            async for doc in connector.iter_all_documents():
                live_ids.add(doc.doc_id)
                if since is not None and doc.updated_at is not None and doc.updated_at <= since:
                    counters["skipped"] += 1
                    continue
                try:
                    with doc_lock(doc.doc_id):
                        # Re-fetch instead of indexing the listing snapshot: `doc`
                        # can be stale by now, and this must win over a racing
                        # webhook write with whatever is actually newest.
                        fresh_doc = await connector.get_document(doc.doc_id)
                        index_document(fresh_doc)
                    counters["indexed"] += 1
                except Exception as exc:
                    print(f"[worker] Failed {doc.doc_id} ({doc.title}): {exc}")
                    counters["failed"] += 1
            return live_ids

        live_ids = asyncio.run(_walk_documents())

        stale_ids = get_all_doc_ids() - live_ids
        for doc_id in stale_ids:
            with doc_lock(doc_id):
                delete_document(doc_id)
        counters["removed"] = len(stale_ids)

        set_last_synced_at(sync_started_at)
    finally:
        release_sync_lock()
        print(
            f"[worker] Sync done — indexed={counters['indexed']}, "
            f"skipped={counters['skipped']}, failed={counters['failed']}, "
            f"removed={counters['removed']}"
        )
