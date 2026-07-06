"""A/B compare gemini-embedding-2 WITH the task/title prefix (shared/embedder.py's
current behaviour) vs WITHOUT it (raw chunk text, no prefix) on real Outline docs.

Indexes a handful of real documents into two throwaway Qdrant collections —
one embedded with the prefix, one without — then runs the same queries
against both and prints top-k results side by side so you can eyeball which
ranks more relevantly.

Usage (from the repo root, with .env populated — same one docker-compose
reads, and Qdrant reachable at QDRANT_URL, e.g. via docker-compose.dev.yml's
published 6333 port):

    python scripts/ab_test_embedding_prefix.py "how do I reset my password" "vpn setup"

With no query arguments, falls back to a few generic sample queries.
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path

# Let this run as a plain `python scripts/ab_test_embedding_prefix.py` from
# anywhere — Python only puts this file's own directory (scripts/) on
# sys.path in that mode, not the repo root where connector/shared/indexer live.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from qdrant_client.models import Distance, PointStruct, VectorParams  # noqa: E402

from connector.outline import OutlineConnector  # noqa: E402
from indexer.chunker import chunk_markdown  # noqa: E402
from shared.embedder import GeminiProvider, _prepare_document, _prepare_query  # noqa: E402
from shared.vector_store import get_client  # noqa: E402

DOC_LIMIT = int(os.getenv("AB_TEST_DOC_LIMIT", "5"))
CHUNKS_PER_DOC_LIMIT = int(os.getenv("AB_TEST_CHUNKS_PER_DOC_LIMIT", "20"))
COLLECTION_WITH_PREFIX = "ab_test_with_prefix"
COLLECTION_NO_PREFIX = "ab_test_no_prefix"

DEFAULT_QUERIES = [
    "how do I get started",
    "설정 방법",
    "troubleshooting steps",
]


async def fetch_sample_docs():
    connector = OutlineConnector(
        base_url=os.getenv("OUTLINE_API_URL") or os.getenv("OUTLINE_BASE_URL"),
        api_key=os.environ["OUTLINE_API_KEY"],
        public_url=os.getenv("OUTLINE_PUBLIC_URL") or os.getenv("OUTLINE_BASE_URL"),
    )
    docs = []
    async for doc in connector.iter_all_documents():
        if doc.text.strip():
            docs.append(doc)
        if len(docs) >= DOC_LIMIT:
            break
    return docs


def recreate_collection(client, name, vector_size):
    existing = {c.name for c in client.get_collections().collections}
    if name in existing:
        client.delete_collection(name)
    client.create_collection(
        collection_name=name,
        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
    )


def index_docs(client, collection_name, provider, docs, use_prefix):
    points = []
    for doc in docs:
        chunks = chunk_markdown(doc.text)[:CHUNKS_PER_DOC_LIMIT]
        for chunk in chunks:
            text = _prepare_document(chunk, doc.title) if use_prefix else chunk
            vector = provider._embed_one(text)
            points.append(
                PointStruct(
                    id=str(uuid.uuid4()),
                    vector=vector,
                    payload={"title": doc.title, "url": doc.url, "chunk_text": chunk},
                )
            )
    if points:
        client.upsert(collection_name=collection_name, points=points)
    return len(points)


def search(client, collection_name, provider, query, use_prefix, limit=5):
    text = _prepare_query(query) if use_prefix else query
    vector = provider._embed_one(text)
    return client.search(collection_name=collection_name, query_vector=vector, limit=limit, with_payload=True)


def print_results(label, hits):
    print(f"  [{label}]")
    if not hits:
        print("    (no results)")
        return
    for rank, hit in enumerate(hits, start=1):
        title = hit.payload.get("title", "")
        snippet = hit.payload.get("chunk_text", "")[:80].replace("\n", " ")
        print(f"    {rank}. score={hit.score:.4f}  {title}  |  {snippet}...")


def main():
    queries = sys.argv[1:] or DEFAULT_QUERIES

    print("Fetching sample documents from Outline...")
    docs = asyncio.run(fetch_sample_docs())
    if not docs:
        print("No documents with text found — nothing to index.")
        return
    print(f"Fetched {len(docs)} document(s): {[d.title for d in docs]}")

    provider = GeminiProvider()
    client = get_client()
    vector_size = provider.get_vector_size()

    print(f"\nIndexing WITH prefix into '{COLLECTION_WITH_PREFIX}'...")
    recreate_collection(client, COLLECTION_WITH_PREFIX, vector_size)
    n_with = index_docs(client, COLLECTION_WITH_PREFIX, provider, docs, use_prefix=True)
    print(f"  {n_with} chunks indexed.")

    print(f"Indexing WITHOUT prefix into '{COLLECTION_NO_PREFIX}'...")
    recreate_collection(client, COLLECTION_NO_PREFIX, vector_size)
    n_without = index_docs(client, COLLECTION_NO_PREFIX, provider, docs, use_prefix=False)
    print(f"  {n_without} chunks indexed.")

    for query in queries:
        print(f"\n=== Query: {query!r} ===")
        hits_with = search(client, COLLECTION_WITH_PREFIX, provider, query, use_prefix=True)
        hits_without = search(client, COLLECTION_NO_PREFIX, provider, query, use_prefix=False)
        print_results("WITH prefix", hits_with)
        print_results("WITHOUT prefix", hits_without)


if __name__ == "__main__":
    main()
