import os

from dotenv import load_dotenv

load_dotenv()

from mcp.server.fastmcp import FastMCP

from shared.embedder import embed_query
from shared.vector_store import search

mcp = FastMCP(
    name="RAG Knowledge Base",
    instructions=(
        "Search the team knowledge base built from Outline documents. "
        "Use search_knowledge to find relevant pages by semantic meaning."
    ),
    # host/port only take effect for the sse transport; FastMCP.run() itself
    # doesn't accept them — they must be set here in the constructor.
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("MCP_PORT", "8080")),
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


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "sse")
    mcp.run(transport=transport)
