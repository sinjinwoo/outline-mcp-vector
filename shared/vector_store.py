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
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")  # None → no auth (local dev)
COLLECTION_NAME = "documents"

_client: QdrantClient | None = None


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
    return _client


def is_collection_empty() -> bool:
    """Return True if the collection doesn't exist or has no indexed points."""
    client = get_client()
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION_NAME not in existing:
        return True
    info = client.get_collection(COLLECTION_NAME)
    return info.points_count == 0


def ensure_collection(vector_size: int) -> None:
    client = get_client()
    existing = {c.name for c in client.get_collections().collections}
    if COLLECTION_NAME not in existing:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        print(f"Created Qdrant collection '{COLLECTION_NAME}' (dim={vector_size})")


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
            "tags": hit.payload.get("tags", []),
            "snippet": hit.payload.get("chunk_text", "")[:500],
            "doc_id": hit.payload.get("doc_id", ""),
        }
        for hit in hits
    ]
