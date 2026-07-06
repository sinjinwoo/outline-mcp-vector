import os

from dotenv import load_dotenv

load_dotenv()

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from shared.embedder import embed_query
from shared.vector_store import search

MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "8080"))

# Comma-separated pool of accepted tokens, same convention as GOOGLE_API_KEYS —
# lets you hand each client its own token and revoke individually.
MCP_AUTH_TOKENS = {t.strip() for t in os.getenv("MCP_AUTH_TOKENS", "").split(",") if t.strip()}

mcp = FastMCP(
    name="RAG Knowledge Base",
    instructions=(
        "Search the team knowledge base built from Outline documents. "
        "Use search_knowledge to find relevant pages by semantic meaning."
    ),
    host=MCP_HOST,
    port=MCP_PORT,
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


class TokenAuthMiddleware(BaseHTTPMiddleware):
    """Rejects any /sse or /messages request whose token isn't in MCP_AUTH_TOKENS.

    Accepts the token as either an `Authorization: Bearer <token>` header or a
    `?token=` query param — plain-`url` MCP clients (e.g. Claude Desktop) can't
    always attach custom headers to an SSE connection, so the query param is
    the fallback that always works.
    """

    async def dispatch(self, request: Request, call_next):
        auth_header = request.headers.get("authorization", "")
        token = auth_header.removeprefix("Bearer ").strip() or request.query_params.get("token", "")
        if token not in MCP_AUTH_TOKENS:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


def build_app():
    if not MCP_AUTH_TOKENS:
        raise RuntimeError(
            "MCP_AUTH_TOKENS is not set — refusing to start an unauthenticated MCP server. "
            "Set at least one token in .env (see .env.example)."
        )
    app = mcp.sse_app()
    app.add_middleware(TokenAuthMiddleware)
    return app


if __name__ == "__main__":
    # SSE-only, on purpose: this is the one transport we expose to the outside
    # world, so instead of mcp.run(transport=...) we take the underlying
    # Starlette app via sse_app() and serve it ourselves — that's the only way
    # to get TokenAuthMiddleware in front of it.
    uvicorn.run(build_app(), host=MCP_HOST, port=MCP_PORT)
