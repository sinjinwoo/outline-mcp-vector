import hashlib
import hmac
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

load_dotenv()

from indexer.sync_lock import is_sync_running
from indexer.tasks import process_webhook_event, run_sync
from shared.sync_state import get_last_synced_at

OUTLINE_WEBHOOK_SECRET = os.getenv("OUTLINE_WEBHOOK_SECRET", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Design: always sync on startup. The Celery worker (not this API
    # process) performs the actual sync, so it survives API restarts/crashes.
    print("[startup] Enqueuing startup sync...")
    run_sync.delay(False)
    yield


app = FastAPI(title="RAG Indexer", lifespan=lifespan)


def _verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected_hex = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={expected_hex}", signature)


@app.post("/webhook/outline")
async def outline_webhook(request: Request):
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

    process_webhook_event.delay(event, doc_id)
    return JSONResponse({"status": "accepted", "event": event, "doc_id": doc_id})


@app.post("/sync/outline")
async def trigger_sync(full: bool = False):
    """Manually trigger a sync of Outline documents (runs on the Celery worker).

    Incremental by default (only documents changed since the last sync are
    re-embedded). Pass ?full=true to force a re-embed of every document.
    """
    if is_sync_running():
        return JSONResponse({"status": "already_running"}, status_code=409)
    run_sync.delay(full)
    return JSONResponse({"status": "queued", "full": full})


@app.get("/sync/status")
async def sync_status():
    last_synced_at = get_last_synced_at()
    return {
        "sync_running": is_sync_running(),
        "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
