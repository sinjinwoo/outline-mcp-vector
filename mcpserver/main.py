import os

from dotenv import load_dotenv

load_dotenv()

import jwt
import uvicorn
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from shared.embedder import embed_query
from shared.vector_store import search

MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "8080"))

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


@mcp.tool()
async def search_knowledge(query: str, limit: int = 5) -> list[dict]:
    """Search the knowledge base for documents relevant to the query.

    Args:
        query: Natural language search query.
        limit: Number of results to return (1-20, default 5).

    Returns:
        List of matching document snippets with title, url, score, and snippet.
    """
    limit = max(1, min(limit, 20))
    query_embedding = embed_query(query)
    results = search(query_embedding, limit=limit)
    return results


def build_app():
    return mcp.streamable_http_app()


if __name__ == "__main__":
    # Streamable HTTP only, on purpose: the legacy dedicated-SSE transport
    # (separate /sse + /messages endpoints) is deprecated MCP-spec-wide and
    # current clients (e.g. Claude Desktop's remote connector) warn on or
    # refuse it — Streamable HTTP's single /mcp endpoint is what they expect
    # now.
    uvicorn.run(build_app(), host=MCP_HOST, port=MCP_PORT)
