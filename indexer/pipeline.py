from connector.base import Document
from indexer.chunker import chunk_markdown
from shared.embedder import embed_passages, get_vector_size
from shared.vector_store import (
    delete_by_doc_id,
    ensure_collection,
    upsert_chunks,
)


def _init_collection() -> None:
    ensure_collection(get_vector_size())


def index_document(doc: Document) -> None:
    """Full indexing pipeline: chunk → embed → upsert into Qdrant."""
    _init_collection()

    chunks = chunk_markdown(doc.text)
    if not chunks:
        delete_by_doc_id(doc.doc_id)
        print(f"[pipeline] Deleted vectors (empty doc): {doc.title} ({doc.doc_id})")
        return

    embeddings = embed_passages(chunks)

    delete_by_doc_id(doc.doc_id)

    metadata = {
        "title": doc.title,
        "source": doc.source,
        "url": doc.url,
        "tags": doc.tags,
        "collection": doc.collection,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }
    upsert_chunks(doc.doc_id, chunks, embeddings, metadata)

    print(f"[pipeline] Indexed {len(chunks)} chunks: {doc.title} ({doc.doc_id})")


def delete_document(doc_id: str) -> None:
    """Remove all vectors for a document from Qdrant."""
    delete_by_doc_id(doc_id)
    print(f"[pipeline] Deleted vectors: {doc_id}")
