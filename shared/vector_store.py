import os
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
# Same variable name Qdrant itself uses to set this key server-side
# (service.api_key -> QDRANT__SERVICE__API_KEY) — kept identical here so
# there's only one name for this secret across the whole stack.
QDRANT_API_KEY = os.getenv("QDRANT__SERVICE__API_KEY")  # None → no auth (local dev)
# MCP spec (Lifecycle, "Timeouts") calls for a timeout on outbound I/O a tool
# handler makes — without one, a stalled Qdrant call hangs the
# search_knowledge request forever with no way to cancel it.
QDRANT_TIMEOUT_SECONDS = float(os.getenv("QDRANT_TIMEOUT_SECONDS", "10"))
COLLECTION_NAME = "documents"

_client: QdrantClient | None = None


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=QDRANT_TIMEOUT_SECONDS)
    return _client


def ensure_collection(vector_size: int) -> None:
    client = get_client()
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION_NAME not in existing:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        print(f"Created Qdrant collection '{COLLECTION_NAME}' (dim={vector_size})")


def get_all_doc_ids() -> set[str]:
    """Return the set of distinct doc_ids currently stored in Qdrant.

    Used to reconcile deletions: any doc_id present here but missing from
    the source connector's live document list has been deleted/archived
    upstream and should be removed.
    """
    client = get_client()
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION_NAME not in existing:
        return set()

    doc_ids: set[str] = set()
    next_offset = None
    while True:
        points, next_offset = client.scroll(
            collection_name=COLLECTION_NAME,
            with_payload=["doc_id"],
            with_vectors=False,
            limit=256,
            offset=next_offset,
        )
        for point in points:
            doc_id = point.payload.get("doc_id")
            if doc_id:
                doc_ids.add(doc_id)
        if next_offset is None:
            break
    return doc_ids


def delete_by_doc_id(doc_id: str) -> None:
    client = get_client()
    client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(
            must=[FieldCondition(key="doc_id", match=MatchValue(value=doc_id))]
        ),
    )


def upsert_chunks(
    doc_id: str,
    chunks: list[str],
    embeddings: list[list[float]],
    metadata: dict,
) -> None:
    client = get_client()
    points = [
        PointStruct(
            id=str(uuid.uuid4()),
            vector=embedding,
            payload={
                "doc_id": doc_id,
                "chunk_index": i,
                "chunk_text": chunk,
                **metadata,
            },
        )
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings))
    ]
    if points:
        client.upsert(collection_name=COLLECTION_NAME, points=points)


def search(query_embedding: list[float], limit: int = 5) -> list[dict]:
    client = get_client()
    hits = client.search(
        collection_name=COLLECTION_NAME,
        query_vector=query_embedding,
        limit=limit,
        with_payload=True,
    )
    return [
        {
            "score": round(hit.score, 4),
            "title": hit.payload.get("title", ""),
            "url": hit.payload.get("url", ""),
            "source": hit.payload.get("source", ""),
            "collection": hit.payload.get("collection", ""),
            "tags": hit.payload.get("tags", []),
            "snippet": hit.payload.get("chunk_text", "")[:500],
            "doc_id": hit.payload.get("doc_id", ""),
        }
        for hit in hits
    ]
