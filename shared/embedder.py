"""Embedding engine with pluggable provider support.

EMBEDDING_PROVIDER=gemini  (default)
    GOOGLE_API_KEYS        - required, comma-separated pool (key1,key2,key3)
                             falls back to GEMINI_API_KEY for a single key
    GEMINI_EMBEDDING_MODEL - default: gemini-embedding-001
    GEMINI_EMBEDDING_DIM   - default: 3072 (output_dimensionality)

EMBEDDING_PROVIDER=openai
    OPENAI_API_KEY         - required
    OPENAI_EMBEDDING_MODEL - default: text-embedding-3-small
"""

import itertools
import os
from abc import ABC, abstractmethod

PROVIDER = os.getenv("EMBEDDING_PROVIDER", "gemini").lower()


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
    """Gemini embedding with a round-robin, auto-retrying API key pool.

    Matches the design doc's Key Pool behaviour: requests rotate through
    GOOGLE_API_KEYS in order (key1 -> key2 -> ... -> keyN -> key1), and a
    failed request (429 or any transient API error) is retried on the next
    key. The whole embed call only fails once every key has been tried.
    """

    _DEFAULT_DIMENSION = 3072  # gemini-embedding-001 native output size

    def __init__(self) -> None:
        import google.generativeai as genai

        raw_keys = os.getenv("GOOGLE_API_KEYS") or os.getenv("GEMINI_API_KEY", "")
        keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        if not keys:
            raise ValueError(
                "GOOGLE_API_KEYS (comma-separated) or GEMINI_API_KEY is required "
                "for the Gemini embedding provider"
            )

        self._genai = genai
        self._keys = keys
        self._key_cycle = itertools.cycle(range(len(keys)))

        model_name = os.getenv("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")
        self._model_path = (
            model_name if model_name.startswith("models/") else f"models/{model_name}"
        )
        self._dimension = int(
            os.getenv("GEMINI_EMBEDDING_DIM", str(self._DEFAULT_DIMENSION))
        )
        print(
            f"[embedder] Using Gemini embedding model: {self._model_path} "
            f"(dim={self._dimension}, {len(keys)} API key(s) in pool)"
        )

    def get_vector_size(self) -> int:
        return self._dimension

    def _call_with_key_rotation(self, make_request):
        """Try `make_request()` once per key, rotating on failure.

        Each key is attempted at most once per call. Raises the last
        error once every key in the pool has failed.
        """
        last_exc: Exception | None = None
        for _ in range(len(self._keys)):
            key_index = next(self._key_cycle)
            self._genai.configure(api_key=self._keys[key_index])
            try:
                return make_request()
            except Exception as exc:
                last_exc = exc
                is_rate_limit = "429" in str(exc) or "quota" in str(exc).lower()
                print(
                    f"[embedder] Gemini key #{key_index} failed"
                    f"{' (rate limited)' if is_rate_limit else ''}, "
                    f"rotating to next key: {exc}"
                )
        raise RuntimeError(f"All {len(self._keys)} Gemini API key(s) failed") from last_exc

    def _embed_one(self, text: str, task_type: str) -> list[float]:
        def make_request():
            result = self._genai.embed_content(
                model=self._model_path,
                content=text,
                task_type=task_type,
                output_dimensionality=self._dimension,
            )
            return result["embedding"]

        return self._call_with_key_rotation(make_request)

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text, "RETRIEVAL_QUERY")

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        # Gemini embed_content handles one text at a time; batch with a loop
        return [self._embed_one(text, "RETRIEVAL_DOCUMENT") for text in texts]


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_provider: EmbeddingProvider | None = None


def _get_provider() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        if PROVIDER == "openai":
            _provider = OpenAIProvider()
        else:
            _provider = GeminiProvider()
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
