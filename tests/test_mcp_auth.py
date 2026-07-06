import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import mcpserver.main as mcp_main


def make_test_app():
    """A trivial app behind TokenAuthMiddleware.

    We deliberately don't drive auth-success assertions through the real
    sse_app() — its /sse route is a long-lived stream that never completes a
    response body, so a plain TestClient request on it hangs. The middleware
    itself doesn't care what's behind it, so a throwaway 200 route is enough
    to prove a valid token is let through.
    """
    app = Starlette(routes=[Route("/sse", lambda request: PlainTextResponse("ok"))])
    app.add_middleware(mcp_main.TokenAuthMiddleware)
    return app


def test_build_app_refuses_to_start_without_tokens(monkeypatch):
    monkeypatch.setattr(mcp_main, "MCP_AUTH_TOKENS", set())

    with pytest.raises(RuntimeError, match="MCP_AUTH_TOKENS"):
        mcp_main.build_app()


def test_rejects_request_with_no_token(monkeypatch):
    monkeypatch.setattr(mcp_main, "MCP_AUTH_TOKENS", {"good-token"})
    client = TestClient(mcp_main.build_app())

    resp = client.get("/sse")

    assert resp.status_code == 401


def test_rejects_request_with_wrong_token(monkeypatch):
    monkeypatch.setattr(mcp_main, "MCP_AUTH_TOKENS", {"good-token"})
    client = TestClient(mcp_main.build_app())

    assert client.get("/sse", headers={"Authorization": "Bearer wrong-token"}).status_code == 401
    assert client.get("/sse?token=wrong-token").status_code == 401


def test_accepts_valid_token_via_bearer_header(monkeypatch):
    monkeypatch.setattr(mcp_main, "MCP_AUTH_TOKENS", {"good-token", "other-token"})
    client = TestClient(make_test_app())

    resp = client.get("/sse", headers={"Authorization": "Bearer good-token"})

    assert resp.status_code == 200


def test_accepts_valid_token_via_query_param(monkeypatch):
    monkeypatch.setattr(mcp_main, "MCP_AUTH_TOKENS", {"good-token", "other-token"})
    client = TestClient(make_test_app())

    resp = client.get("/sse?token=other-token")

    assert resp.status_code == 200
