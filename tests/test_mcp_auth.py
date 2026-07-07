import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from starlette.testclient import TestClient

import mcpserver.main as mcp_main

INITIALIZE_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}

ISSUER = "https://keycloak.example.com/realms/test"
AUDIENCE = "outline-mcp"


@pytest.fixture(autouse=True)
def _fresh_session_manager():
    """FastMCP caches its StreamableHTTPSessionManager on first use and that
    manager's .run() can only be entered once ever — reused across tests it
    raises on the second TestClient(...) __enter__. mcp is a single
    module-level instance shared by every test in this file, so reset the
    cache before each test to force a fresh manager."""
    mcp_main.mcp._session_manager = None


def _set_transport_security(monkeypatch, **kwargs):
    """Swap the shared FastMCP instance's transport_security for one test."""
    monkeypatch.setattr(mcp_main.mcp.settings, "transport_security", TransportSecuritySettings(**kwargs))


@pytest.fixture(scope="module")
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _make_token(private_key, **claim_overrides):
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-1",
        "azp": "test-client",
        "scope": "mcp:search",
        "exp": int(time.time()) + 300,
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, private_key, algorithm="RS256")


def _enable_oauth(monkeypatch, public_key):
    """Turn on the OAuth (Keycloak) resource-server path for one test, with
    the JWKS fetch faked out so tests don't need a live Keycloak — the point
    under test is the JWT verification logic (signature/issuer/audience/exp),
    not PyJWKClient's HTTP fetch."""
    verifier = mcp_main.KeycloakTokenVerifier(jwks_url="https://unused.invalid/certs", issuer=ISSUER, audience=AUDIENCE)
    monkeypatch.setattr(verifier, "_jwks_client", SimpleNamespace(get_signing_key_from_jwt=lambda token: SimpleNamespace(key=public_key)))
    monkeypatch.setattr(mcp_main.mcp, "_token_verifier", verifier)
    monkeypatch.setattr(
        mcp_main.mcp.settings,
        "auth",
        AuthSettings(issuer_url=ISSUER, resource_server_url="https://mcp.example.com"),
    )


def test_open_by_default_when_oauth_disabled(monkeypatch):
    # MCP_OAUTH_ENABLED is false in the test environment, so the module-level
    # mcp instance was built with no auth at all — no Authorization header
    # should be required.
    assert mcp_main.mcp._token_verifier is None
    with TestClient(mcp_main.build_app()) as client:
        resp = client.post("/mcp", json=INITIALIZE_BODY, headers=MCP_HEADERS)

    assert resp.status_code == 200
    assert "RAG Knowledge Base" in resp.text


def test_rejects_missing_bearer_token_when_oauth_enabled(monkeypatch, rsa_keypair):
    _, public_key = rsa_keypair
    _enable_oauth(monkeypatch, public_key)

    with TestClient(mcp_main.build_app()) as client:
        resp = client.post("/mcp", json=INITIALIZE_BODY, headers=MCP_HEADERS)

    assert resp.status_code == 401


def test_rejects_token_with_wrong_signature_when_oauth_enabled(monkeypatch, rsa_keypair):
    _, public_key = rsa_keypair
    _enable_oauth(monkeypatch, public_key)
    # Signed with a different keypair than the one the verifier is faked to
    # trust — must fail signature verification regardless of claim content.
    other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = _make_token(other_private_key)

    with TestClient(mcp_main.build_app()) as client:
        resp = client.post(
            "/mcp", json=INITIALIZE_BODY, headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"}
        )

    assert resp.status_code == 401


def test_rejects_token_with_wrong_audience_when_oauth_enabled(monkeypatch, rsa_keypair):
    private_key, public_key = rsa_keypair
    _enable_oauth(monkeypatch, public_key)
    token = _make_token(private_key, aud="some-other-client")

    with TestClient(mcp_main.build_app()) as client:
        resp = client.post(
            "/mcp", json=INITIALIZE_BODY, headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"}
        )

    assert resp.status_code == 401


def test_rejects_expired_token_when_oauth_enabled(monkeypatch, rsa_keypair):
    private_key, public_key = rsa_keypair
    _enable_oauth(monkeypatch, public_key)
    token = _make_token(private_key, exp=int(time.time()) - 60)

    with TestClient(mcp_main.build_app()) as client:
        resp = client.post(
            "/mcp", json=INITIALIZE_BODY, headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"}
        )

    assert resp.status_code == 401


def test_accepts_valid_keycloak_token_when_oauth_enabled(monkeypatch, rsa_keypair):
    private_key, public_key = rsa_keypair
    _enable_oauth(monkeypatch, public_key)
    token = _make_token(private_key)

    with TestClient(mcp_main.build_app()) as client:
        resp = client.post(
            "/mcp", json=INITIALIZE_BODY, headers={**MCP_HEADERS, "Authorization": f"Bearer {token}"}
        )

    assert resp.status_code == 200
    assert "RAG Knowledge Base" in resp.text


def test_rejects_request_with_disallowed_host(monkeypatch):
    _set_transport_security(
        monkeypatch,
        enable_dns_rebinding_protection=True,
        allowed_hosts=["allowed.example.com"],
        allowed_origins=[],
    )
    with TestClient(mcp_main.build_app()) as client:
        resp = client.post("/mcp", json=INITIALIZE_BODY, headers=MCP_HEADERS)

    # TestClient's default Host header is "testserver", which isn't in
    # allowed_hosts above — the DNS rebinding guard must reject it with 421.
    assert resp.status_code == 421


def test_rejects_request_with_disallowed_origin(monkeypatch):
    _set_transport_security(
        monkeypatch,
        enable_dns_rebinding_protection=True,
        allowed_hosts=["testserver"],
        allowed_origins=["https://allowed.example.com"],
    )
    with TestClient(mcp_main.build_app()) as client:
        resp = client.post(
            "/mcp",
            json=INITIALIZE_BODY,
            headers={**MCP_HEADERS, "Origin": "https://evil.example.com"},
        )

    assert resp.status_code == 403


def test_accepts_request_with_allowed_host_and_origin(monkeypatch):
    _set_transport_security(
        monkeypatch,
        enable_dns_rebinding_protection=True,
        allowed_hosts=["testserver"],
        allowed_origins=["https://allowed.example.com"],
    )
    with TestClient(mcp_main.build_app()) as client:
        resp = client.post(
            "/mcp",
            json=INITIALIZE_BODY,
            headers={**MCP_HEADERS, "Origin": "https://allowed.example.com"},
        )

    assert resp.status_code == 200
