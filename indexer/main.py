import asyncio
import hashlib
import hmac
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

load_dotenv()

from connector.outline import OutlineConnector
from indexer.pipeline import delete_document, index_document
from shared.vector_store import is_collection_empty

OUTLINE_BASE_URL = os.getenv("OUTLINE_BASE_URL", "")
OUTLINE_API_KEY = os.getenv("OUTLINE_API_KEY", "")
OUTLINE_WEBHOOK_SECRET = os.getenv("OUTLINE_WEBHOOK_SECRET", "")

connector = OutlineConnector(base_url=OUTLINE_BASE_URL, api_key=OUTLINE_API_KEY)

INDEX_EVENTS = {"documents.create", "documents.update", "documents.publish"}
DELETE_EVENTS = {"documents.delete", "documents.archive"}

_sync_running = False


async def _full_sync() -> None:
    global _sync_running
    if _sync_running:
        return

    _sync_running = True
    indexed = failed = 0
    try:
        async for doc in connector.iter_all_documents():
            try:
                index_document(doc)
                indexed += 1
            except Exception as exc:
                print(f"[sync] Failed {doc.doc_id} ({doc.title}): {exc}")
                failed += 1
    finally:
        _sync_running = False
        print(f"[sync] Done — indexed={indexed}, failed={failed}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-sync only when Qdrant is empty (first run or after volume wipe)
    if is_collection_empty():
        print("[startup] Qdrant is empty — starting initial full sync...")
        asyncio.create_task(_full_sync())
    else:
        print("[startup] Qdrant already has data — skipping initial sync.")
    yield


app = FastAPI(title="RAG Indexer", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected_hex = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected_hex}", signature)


async def _process_event(event: str, doc_id: str) -> None:
    try:
        if event in INDEX_EVENTS:
            doc = await connector.get_document(doc_id)
            index_document(doc)
        elif event in DELETE_EVENTS:
            delete_document(doc_id)
        else:
            print(f"[webhook] Ignored event: {event}")
    except Exception as exc:
        print(f"[webhook] Error processing {event} for {doc_id}: {exc}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/webhook/outline")
async def outline_webhook(request: Request, background_tasks: BackgroundTasks):
    body = await request.body()

    if OUTLINE_WEBHOOK_SECRET:
        signature = request.headers.get("outline-signature", "")
        if not _verify_signature(body, signature, OUTLINE_WEBHOOK_SECRET):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = await request.json()
    event: str = payload.get("event", "")
    doc_id: str = payload.get("payload", {}).get("id", "")

    if not doc_id:
        return JSONResponse({"status": "ignored", "reason": "no document id"})

    background_tasks.add_task(_process_event, event, doc_id)
    return JSONResponse({"status": "accepted", "event": event, "doc_id": doc_id})


@app.post("/sync/outline")
async def trigger_sync(background_tasks: BackgroundTasks):
    """Manually trigger a full re-index of all Outline documents."""
    if _sync_running:
        return JSONResponse({"status": "already_running"}, status_code=409)
    background_tasks.add_task(_full_sync)
    return JSONResponse({"status": "started"})


@app.get("/sync/status")
async def sync_status():
    return {"sync_running": _sync_running}


@app.get("/health")
async def health():
    return {"status": "ok"}
