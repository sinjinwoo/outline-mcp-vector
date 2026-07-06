from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Document:
    source: str
    doc_id: str
    title: str
    text: str
    url: str
    tags: list[str] = field(default_factory=list)
    collection: Optional[str] = None
    updated_at: Optional[datetime] = None


class Connector(ABC):
    @abstractmethod
    async def get_document(self, doc_id: str) -> Document:
        pass
