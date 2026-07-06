"""Persists the incremental-sync cursor (last successful sync time) to disk.

Stored outside Qdrant so the cursor survives even if the vector collection
is dropped/recreated, and so we don't need a Qdrant round-trip just to know
when we last synced.
"""

import json
import os
from datetime import datetime
from pathlib import Path

_STATE_PATH = Path(os.getenv("SYNC_STATE_PATH", "/data/sync_state.json"))


def get_last_synced_at() -> datetime | None:
    if not _STATE_PATH.exists():
        return None
    try:
        raw = json.loads(_STATE_PATH.read_text())
        return datetime.fromisoformat(raw["last_synced_at"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


def set_last_synced_at(dt: datetime) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps({"last_synced_at": dt.isoformat()}))
