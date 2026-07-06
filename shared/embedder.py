"""Gemini embedding engine.

GOOGLE_API_KEYS      - required, comma-separated pool (key1,key2,key3)
                       falls back to GEMINI_API_KEY for a single key
GEMINI_EMBEDDING_DIM - default: 3072 (output_dimensionality)

Always uses the gemini-embedding-2 model — there's no other provider or model
to choose, so it isn't configurable.

Uses the `google-genai` SDK, not the deprecated `google.generativeai` package
(support for that one ended and gemini-embedding-* isn't served on its v1beta
API anymore — every call 404s regardless of key).

gemini-embedding-2 takes task instructions as a text prefix baked into the
input itself rather than an API parameter (see Google's asymmetric-retrieval
guidance for this model) — _prepare_query/_prepare_document below. Because
it's asymmetric, a query embedding is only meaningfully comparable against
document embeddings that went through _prepare_document; the two prefixes
must not be swapped or dropped.
"""

import itertools
import os

_MODEL = "gemini-embedding-2"
_DEFAULT_DIMENSION = 3072


def _prepare_query(text: str) -> str:
    return f"task: search result | query: {text}"


def _prepare_document(text: str, title: str | None) -> str:
    return f"title: {title or 'none'} | text: {text}"


class GeminiProvider:
    """Gemini embedding with a round-robin, auto-retrying API key pool.

    Matches the design doc's Key Pool behaviour: requests rotate through
    GOOGLE_API_KEYS in order (key1 -> key2 -> ... -> keyN -> key1), and a
    failed request (429 or any transient API error) is retried on the next
    key. The whole embed call only fails once every key has been tried.
    """

    def __init__(self) -> None:
        from google import genai
        from google.genai import types

        raw_keys = os.getenv("GOOGLE_API_KEYS") or os.getenv("GEMINI_API_KEY", "")
        keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        if not keys:
            raise ValueError(
                "GOOGLE_API_KEYS (comma-separated) or GEMINI_API_KEY is required "
                "for the Gemini embedding provider"
            )

        self._types = types
        # google-genai is client-based (no global genai.configure()), so each
        # key gets its own Client up front instead of reconfiguring a
        # module-level singleton before every call.
        self._clients = [genai.Client(api_key=key) for key in keys]
        self._key_cycle = itertools.cycle(range(len(keys)))
        self._dimension = int(os.getenv("GEMINI_EMBEDDING_DIM", str(_DEFAULT_DIMENSION)))
        print(
            f"[embedder] Using Gemini embedding model: {_MODEL} "
            f"(dim={self._dimension}, {len(keys)} API key(s) in pool)"
        )

    def get_vector_size(self) -> int:
        return self._dimension

    def _call_with_key_rotation(self, make_request):
        """Try `make_request(client)` once per key, rotating on failure.

        Each key is attempted at most once per call. Raises the last
        error once every key in the pool has failed.
        """
        last_exc: Exception | None = None
        for _ in range(len(self._clients)):
            key_index = next(self._key_cycle)
            try:
                return make_request(self._clients[key_index])
            except Exception as exc:
                last_exc = exc
                is_rate_limit = "429" in str(exc) or "quota" in str(exc).lower()
                print(
                    f"[embedder] Gemini key #{key_index} failed"
                    f"{' (rate limited)' if is_rate_limit else ''}, "
                    f"rotating to next key: {exc}"
                )
        raise RuntimeError(f"All {len(self._clients)} Gemini API key(s) failed") from last_exc

    def _embed_one(self, text: str) -> list[float]:
        def make_request(client):
            result = client.models.embed_content(
                model=_MODEL,
                contents=text,
                config=self._types.EmbedContentConfig(output_dimensionality=self._dimension),
            )
            return result.embeddings[0].values

        return self._call_with_key_rotation(make_request)

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(_prepare_query(text))

    def embed_passages(self, texts: list[str], title: str | None = None) -> list[list[float]]:
        # Gemini embed_content handles one text at a time; batch with a loop
        return [self._embed_one(_prepare_document(text, title)) for text in texts]


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_provider: GeminiProvider | None = None


def _get_provider() -> GeminiProvider:
    global _provider
    if _provider is None:
        _provider = GeminiProvider()
    return _provider


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_vector_size() -> int:
    return _get_provider().get_vector_size()


def embed_query(text: str) -> list[float]:
    return _get_provider().embed_query(text)


def embed_passages(texts: list[str], title: str | None = None) -> list[list[float]]:
    return _get_provider().embed_passages(texts, title)
