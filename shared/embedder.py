"""Embedding engine with pluggable provider support.

EMBEDDING_PROVIDER=huggingface  (default)
    HF_MODEL   - HuggingFace model name (default: intfloat/multilingual-e5-base)
    HF_DEVICE  - cpu | cuda (default: cpu)

EMBEDDING_PROVIDER=openai
    OPENAI_API_KEY         - required
    OPENAI_EMBEDDING_MODEL - default: text-embedding-3-small

EMBEDDING_PROVIDER=gemini
    GEMINI_API_KEY         - required
    GEMINI_EMBEDDING_MODEL - default: models/text-embedding-004
"""

import os
from abc import ABC, abstractmethod

PROVIDER = os.getenv("EMBEDDING_PROVIDER", "huggingface").lower()


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class EmbeddingProvider(ABC):
    @abstractmethod
    def get_vector_size(self) -> int: ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]: ...

    @abstractmethod
    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...


# ---------------------------------------------------------------------------
# HuggingFace provider
# ---------------------------------------------------------------------------

class HuggingFaceProvider(EmbeddingProvider):
    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer

        model_name = os.getenv("HF_MODEL", "intfloat/multilingual-e5-base")
        device = os.getenv("HF_DEVICE", "cpu")
        print(f"[embedder] Loading HuggingFace model: {model_name} on {device}")
        self._model = SentenceTransformer(model_name, device=device)
        self._is_e5 = "e5" in model_name.lower()

    def get_vector_size(self) -> int:
        return self._model.get_sentence_embedding_dimension()

    def embed_query(self, text: str) -> list[float]:
        prefixed = f"query: {text}" if self._is_e5 else text
        return self._model.encode(prefixed, normalize_embeddings=True).tolist()

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        prefixed = [f"passage: {t}" if self._is_e5 else t for t in texts]
        return self._model.encode(
            prefixed, normalize_embeddings=True, batch_size=32
        ).tolist()


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------

class OpenAIProvider(EmbeddingProvider):
    _DIMENSIONS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(self) -> None:
        from openai import OpenAI

        self._model_name = os.getenv(
            "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
        )
        self._client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        print(f"[embedder] Using OpenAI embedding model: {self._model_name}")

    def get_vector_size(self) -> int:
        return self._DIMENSIONS.get(self._model_name, 1536)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        response = self._client.embeddings.create(
            model=self._model_name,
            input=texts,
        )
        return [item.embedding for item in response.data]

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for i in range(0, len(texts), 256):
            results.extend(self._embed(texts[i : i + 256]))
        return results


# ---------------------------------------------------------------------------
# Gemini provider
# ---------------------------------------------------------------------------

class GeminiProvider(EmbeddingProvider):
    # models/text-embedding-004: 768-dim
    _DIMENSIONS = {
        "models/text-embedding-004": 768,
        "models/embedding-001": 768,
    }

    def __init__(self) -> None:
        import google.generativeai as genai

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required for Gemini embedding provider")
        genai.configure(api_key=api_key)
        self._genai = genai
        self._model_name = os.getenv(
            "GEMINI_EMBEDDING_MODEL", "models/text-embedding-004"
        )
        print(f"[embedder] Using Gemini embedding model: {self._model_name}")

    def get_vector_size(self) -> int:
        return self._DIMENSIONS.get(self._model_name, 768)

    def embed_query(self, text: str) -> list[float]:
        result = self._genai.embed_content(
            model=self._model_name,
            content=text,
            task_type="RETRIEVAL_QUERY",
        )
        return result["embedding"]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        # Gemini embed_content handles one text at a time; batch with a loop
        embeddings: list[list[float]] = []
        for text in texts:
            result = self._genai.embed_content(
                model=self._model_name,
                content=text,
                task_type="RETRIEVAL_DOCUMENT",
            )
            embeddings.append(result["embedding"])
        return embeddings


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_provider: EmbeddingProvider | None = None


def _get_provider() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        if PROVIDER == "openai":
            _provider = OpenAIProvider()
        elif PROVIDER == "gemini":
            _provider = GeminiProvider()
        else:
            _provider = HuggingFaceProvider()
    return _provider


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_vector_size() -> int:
    return _get_provider().get_vector_size()


def embed_query(text: str) -> list[float]:
    return _get_provider().embed_query(text)


def embed_passages(texts: list[str]) -> list[list[float]]:
    return _get_provider().embed_passages(texts)
