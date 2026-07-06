from datetime import datetime
from typing import AsyncIterator

import httpx

from .base import Connector, Document

_PAGE_LIMIT = 25  # Outline API max per page


class OutlineConnector(Connector):
    def __init__(self, base_url: str, api_key: str, public_url: str | None = None):
        """
        base_url: used for actual API calls. When the connector runs on the
            same Docker network as Outline, this can be an internal hostname
            (e.g. http://outline:3000) to skip the public round-trip.
        public_url: used to build the doc URLs shown in search results,
            which must stay externally clickable. Defaults to base_url when
            not co-located with Outline (the common case).
        """
        self.base_url = base_url.rstrip("/")
        self.public_url = (public_url or base_url).rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._collection_names: dict[str, str] = {}  # collectionId -> name

    def _parse_document(self, data: dict) -> Document:
        url = data.get("url", "")
        if url and not url.startswith("http"):
            url = f"{self.public_url}{url}"

        updated_at = None
        if data.get("updatedAt"):
            updated_at = datetime.fromisoformat(
                data["updatedAt"].replace("Z", "+00:00")
            )

        collection_id = data.get("collectionId")
        collection_name = self._collection_names.get(collection_id, collection_id)

        return Document(
            source="outline",
            doc_id=data["id"],
            title=data["title"],
            text=data.get("text", ""),
            url=url,
            tags=[tag["name"] for tag in data.get("tags", [])],
            collection=collection_name,
            updated_at=updated_at,
        )

    async def list_collections(self, client: httpx.AsyncClient | None = None) -> dict[str, str]:
        """Fetch all collections and cache collectionId -> name.

        Outline's documents.list only returns a collectionId (an opaque
        UUID), never a human-readable name, so this must be resolved
        separately via collections.list.
        """
        owns_client = client is None
        client = client or httpx.AsyncClient()
        try:
            offset = 0
            names: dict[str, str] = {}
            while True:
                resp = await client.post(
                    f"{self.base_url}/api/collections.list",
                    headers=self.headers,
                    json={"limit": _PAGE_LIMIT, "offset": offset},
                    timeout=30.0,
                )
                resp.raise_for_status()
                body = resp.json()
                items = body.get("data", [])
                for item in items:
                    names[item["id"]] = item["name"]

                total: int = body.get("pagination", {}).get("total", 0)
                offset += len(items)
                if offset >= total or not items:
                    break

            self._collection_names = names
            return dict(names)
        finally:
            if owns_client:
                await client.aclose()

    async def get_document(self, doc_id: str) -> Document:
        async with httpx.AsyncClient() as client:
            if not self._collection_names:
                await self.list_collections(client)
            resp = await client.post(
                f"{self.base_url}/api/documents.info",
                headers=self.headers,
                json={"id": doc_id},
                timeout=30.0,
            )
            resp.raise_for_status()
            return self._parse_document(resp.json()["data"])

    async def iter_all_documents(self) -> AsyncIterator[Document]:
        """Paginate through every published document in the Outline workspace."""
        offset = 0
        async with httpx.AsyncClient() as client:
            if not self._collection_names:
                await self.list_collections(client)

            while True:
                resp = await client.post(
                    f"{self.base_url}/api/documents.list",
                    headers=self.headers,
                    json={
                        "limit": _PAGE_LIMIT,
                        "offset": offset,
                        # Only fetch published (non-draft) documents
                        "statusFilter": ["published"],
                    },
                    timeout=30.0,
                )
                resp.raise_for_status()
                body = resp.json()
                docs = body.get("data", [])

                for raw in docs:
                    # Skip templates and archived docs
                    if raw.get("template") or raw.get("archivedAt"):
                        continue
                    yield self._parse_document(raw)

                total: int = body.get("pagination", {}).get("total", 0)
                offset += len(docs)
                if offset >= total or not docs:
                    break
