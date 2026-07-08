import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

import httpx
import jwt
import uvicorn
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from connector.outline import OutlineConnector
from shared.embedder import embed_query
from shared.vector_store import search

MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "8080"))

# Same fallback pattern as indexer/tasks.py: OUTLINE_API_URL for co-located
# internal networking, falling back to the public OUTLINE_BASE_URL.
OUTLINE_API_URL = os.getenv("OUTLINE_API_URL") or os.getenv("OUTLINE_BASE_URL", "")
OUTLINE_API_KEY = os.getenv("OUTLINE_API_KEY", "")
_outline = OutlineConnector(base_url=OUTLINE_API_URL, api_key=OUTLINE_API_KEY)

# Hard cap on how many vector-search candidates search_knowledge will ever
# verify against Outline for one query, regardless of how many end up
# rejected — bounds the cost of a query independent of corpus size.
MAX_ACCESS_CHECK_CANDIDATES = 40

# Comma-separated allow-lists for DNS rebinding protection (MCP transport spec
# MUST). The SDK's TransportSecuritySettings only turns itself on by default
# when FastMCP is bound to a loopback host — this project binds 0.0.0.0 in
# production, so without an explicit allow-list it stays silently off. Left
# empty, protection stays disabled, since turning it on with no allowed hosts
# would 421 every request including the real deployment's own domain.
MCP_ALLOWED_HOSTS = {h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()}
MCP_ALLOWED_ORIGINS = {o.strip() for o in os.getenv("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()}

# Keycloak/OAuth auth is opt-in. Default is a fully open server (no auth at
# all) — whoever deploys this decides whether to require a Keycloak-issued
# Bearer token by setting MCP_OAUTH_ENABLED=true plus the three vars below.
# This project never runs its own auth server; it only ever acts as an OAuth
# Resource Server that checks a signature against Keycloak's JWKS endpoint.
MCP_OAUTH_ENABLED = os.getenv("MCP_OAUTH_ENABLED", "false").strip().lower() == "true"
MCP_OAUTH_ISSUER_URL = os.getenv("MCP_OAUTH_ISSUER_URL", "")
MCP_OAUTH_RESOURCE_URL = os.getenv("MCP_OAUTH_RESOURCE_URL", "")
MCP_OAUTH_AUDIENCE = os.getenv("MCP_OAUTH_AUDIENCE", "")
MCP_OAUTH_JWKS_URL = os.getenv("MCP_OAUTH_JWKS_URL", "")


class KeycloakTokenVerifier(TokenVerifier):
    """Validates Bearer tokens as JWTs signed by an external Keycloak realm.

    Resource-server-only role per the MCP Authorization spec: this project
    never issues, stores, or introspects tokens itself — it only verifies the
    signature against Keycloak's JWKS endpoint. PyJWKClient caches the JWKS
    response (default 5 min) so this isn't a network round trip per request.
    """

    def __init__(self, jwks_url: str, issuer: str, audience: str) -> None:
        self._jwks_client = PyJWKClient(jwks_url)
        self._issuer = issuer
        self._audience = audience

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
            )
        except jwt.PyJWTError:
            return None

        return AccessToken(
            token=token,
            client_id=claims.get("azp") or claims.get("sub", ""),
            scopes=claims.get("scope", "").split(),
            expires_at=claims.get("exp"),
            subject=claims.get("sub"),
            claims=claims,
        )


def _build_oauth_settings() -> tuple[AuthSettings, KeycloakTokenVerifier]:
    if not (MCP_OAUTH_ISSUER_URL and MCP_OAUTH_RESOURCE_URL and MCP_OAUTH_AUDIENCE):
        raise RuntimeError(
            "MCP_OAUTH_ENABLED=true requires MCP_OAUTH_ISSUER_URL, MCP_OAUTH_RESOURCE_URL, "
            "and MCP_OAUTH_AUDIENCE to all be set (see .env.example)."
        )
    jwks_url = MCP_OAUTH_JWKS_URL or f"{MCP_OAUTH_ISSUER_URL.rstrip('/')}/protocol/openid-connect/certs"
    verifier = KeycloakTokenVerifier(jwks_url=jwks_url, issuer=MCP_OAUTH_ISSUER_URL, audience=MCP_OAUTH_AUDIENCE)
    auth_settings = AuthSettings(issuer_url=MCP_OAUTH_ISSUER_URL, resource_server_url=MCP_OAUTH_RESOURCE_URL)
    return auth_settings, verifier


_auth_settings, _token_verifier = _build_oauth_settings() if MCP_OAUTH_ENABLED else (None, None)

mcp = FastMCP(
    name="RAG Knowledge Base",
    instructions=(
        "Search the team knowledge base built from Outline documents. "
        "Use search_knowledge to find relevant pages by semantic meaning."
    ),
    host=MCP_HOST,
    port=MCP_PORT,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=bool(MCP_ALLOWED_HOSTS),
        allowed_hosts=list(MCP_ALLOWED_HOSTS),
        allowed_origins=list(MCP_ALLOWED_ORIGINS),
    ),
    auth=_auth_settings,
    token_verifier=_token_verifier,
)

if MCP_OAUTH_ENABLED:
    # The SDK only serves RFC 9728 protected-resource metadata at
    # /.well-known/oauth-protected-resource (bare, not under the resource's
    # own /mcp path, despite what build_resource_metadata_url's docstring
    # implies — confirmed empirically against this SDK version). Claude
    # Desktop's OAuth client is fine with that. Claude Code's, as of mid-2026,
    # instead only probes {resource_path}/.well-known/oauth-protected-resource
    # (i.e. /mcp/.well-known/oauth-protected-resource) and never falls back to
    # the bare path, so without this it can't discover Keycloak as the
    # authorization server at all. Mirror the same document at that path too
    # rather than relying on client-specific behavior to line up.
    @mcp.custom_route(f"{mcp.settings.streamable_http_path}/.well-known/oauth-protected-resource", methods=["GET"])
    async def protected_resource_metadata_compat(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "resource": MCP_OAUTH_RESOURCE_URL,
                "authorization_servers": [MCP_OAUTH_ISSUER_URL],
                "bearer_methods_supported": ["header"],
            }
        )


class _OutlineAuthFailure(Exception):
    """Raised when documents.info returns 401 — the key itself is dead, not
    just this one document's permission."""


async def _check_access(doc_id: str) -> bool:
    try:
        await _outline.check_access(doc_id)
        return True
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            raise _OutlineAuthFailure from exc
        return False  # 403/404 — no longer visible to this key
    except httpx.HTTPError:
        return False  # timeout/network/5xx — exclude just this one candidate


_AUTH_FAILURE_MESSAGE = (
    "Outline access check failed with 401 — the connector's API key appears "
    "to be invalid. Search is unavailable until this is fixed."
)


async def _verify_all(batch: list[dict]) -> list[dict]:
    """Verify every candidate in a small batch (size == limit) — every
    result is needed to know whether a backfill round is required, so there's
    no early-stop benefit here."""
    results = await asyncio.gather(
        *(_check_access(c["doc_id"]) for c in batch),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, _OutlineAuthFailure):
            raise RuntimeError(_AUTH_FAILURE_MESSAGE)
    return [c for c, r in zip(batch, results) if r is True]


async def _verify_until_enough(batch: list[dict], needed: int) -> list[dict]:
    """Verify a backfill batch concurrently, but stop and cancel the rest of
    the in-flight checks as soon as `needed` candidates have passed — avoids
    burning extra Outline calls once the query already has enough results."""
    if needed <= 0:
        return []
    pending = {asyncio.create_task(_check_access(c["doc_id"])): c for c in batch}
    verified: list[dict] = []
    try:
        while pending and len(verified) < needed:
            done, _ = await asyncio.wait(pending.keys(), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                candidate = pending.pop(task)
                exc = task.exception()
                if exc is not None:
                    if isinstance(exc, _OutlineAuthFailure):
                        raise RuntimeError(_AUTH_FAILURE_MESSAGE)
                    continue  # network/5xx on this one candidate — excluded, not fatal
                if task.result():
                    verified.append(candidate)
        return verified
    finally:
        for task in pending:
            task.cancel()
            # Some of these may already be done (completed in the same
            # asyncio.wait() batch as the one that triggered the early
            # return/raise, but never reached in the for-loop above) — consume
            # their result/exception so asyncio doesn't warn about an
            # exception that was never retrieved.
            if task.done():
                task.exception()


@mcp.tool()
async def search_knowledge(query: str, limit: int = 5) -> list[dict]:
    """Search the knowledge base for documents relevant to the query.

    Every candidate's Outline access is re-checked live before it's returned,
    so documents whose permissions changed since the last index sync (or
    were removed from Outline entirely) don't leak through stale vectors.

    Args:
        query: Natural language search query.
        limit: Number of results to return (1-20, default 5).

    Returns:
        List of matching document snippets with title, url, score, and snippet.
    """
    limit = max(1, min(limit, 20))
    query_embedding = embed_query(query)

    first_batch = search(query_embedding, limit=limit)
    verified = await _verify_all(first_batch)

    if len(verified) < limit and limit < MAX_ACCESS_CHECK_CANDIDATES:
        extra_batch = search(
            query_embedding,
            limit=MAX_ACCESS_CHECK_CANDIDATES - limit,
            offset=limit,
        )
        verified += await _verify_until_enough(extra_batch, needed=limit - len(verified))

    verified.sort(key=lambda c: c["score"], reverse=True)
    return verified[:limit]


def build_app():
    return mcp.streamable_http_app()


if __name__ == "__main__":
    # Streamable HTTP only, on purpose: the legacy dedicated-SSE transport
    # (separate /sse + /messages endpoints) is deprecated MCP-spec-wide and
    # current clients (e.g. Claude Desktop's remote connector) warn on or
    # refuse it — Streamable HTTP's single /mcp endpoint is what they expect
    # now.
    uvicorn.run(build_app(), host=MCP_HOST, port=MCP_PORT)
