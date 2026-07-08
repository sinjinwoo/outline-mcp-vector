from datetime import datetime, timezone

from starlette.testclient import TestClient

import indexer.main as main_module


def _client(monkeypatch):
    # Avoid touching a real Celery broker on FastAPI startup/manual trigger.
    monkeypatch.setattr(main_module.run_sync, "delay", lambda *a, **kw: None)
    return TestClient(main_module.app)


def test_sync_status_reports_last_synced_at(monkeypatch):
    last_synced = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(main_module, "get_last_synced_at", lambda: last_synced)
    monkeypatch.setattr(main_module, "is_sync_running", lambda: False)

    with _client(monkeypatch) as client:
        resp = client.get("/sync/status")

    assert resp.status_code == 200
    assert resp.json() == {
        "sync_running": False,
        "last_synced_at": last_synced.isoformat(),
    }


def test_sync_status_reports_null_when_never_synced(monkeypatch):
    monkeypatch.setattr(main_module, "get_last_synced_at", lambda: None)
    monkeypatch.setattr(main_module, "is_sync_running", lambda: False)

    with _client(monkeypatch) as client:
        resp = client.get("/sync/status")

    assert resp.status_code == 200
    assert resp.json() == {"sync_running": False, "last_synced_at": None}
