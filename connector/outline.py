from datetime import datetime
from typing import AsyncIterator

import httpx

from .base import Connector, Document

_PAGE_LIMIT = 25  # Outline API max per page


class OutlineConnector(Connector):
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _parse_document(self, data: dict) -> Document:
        url = data.get("url", "")
        if url and not url.startswith("http"):
            url = f"{self.base_url}{url}"

        updated_at = None
        if data.get("updatedAt"):
            updated_at = datetime.fromisoformat(
                data["updatedAt"].replace("Z", "+00:00")
            )

        return Document(
            source="outline",
            doc_id=data["id"],
            title=data["title"],
            text=data.get("text", ""),
            url=url,
            tags=[tag["name"] for tag in data.get("tags", [])],
            collection=data.get("collectionId"),
            updated_at=updated_at,
        )

    async def get_document(self, doc_id: str) -> Document:
        async with httpx.AsyncClient() as client:
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
