import sys
import types
from types import SimpleNamespace

import pytest


class FakeClient:
    """Stand-in for a google.genai.Client — one instance per API key."""

    def __init__(self, fake_genai, api_key, http_options=None):
        self._fake = fake_genai
        self.api_key = api_key
        self.http_options = http_options
        self.models = self  # client.models.embed_content(...) -> self.embed_content(...)

    def embed_content(self, model, contents, config):
        self._fake.calls.append((self.api_key, contents))
        if self.api_key in self._fake.fail_keys:
            raise RuntimeError("429 rate limited")
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1, 0.2, 0.3])])


class FakeGenAIModule(types.ModuleType):
    """Stand-in for google.genai, controllable per-test."""

    def __init__(self):
        super().__init__("google.genai")
        self.calls: list[tuple[str, str]] = []
        self.fail_keys: set[str] = set()

    def Client(self, api_key, http_options=None):
        return FakeClient(self, api_key, http_options)


class FakeTypesModule(types.ModuleType):
    """Stand-in for google.genai.types."""

    def __init__(self):
        super().__init__("google.genai.types")

    def EmbedContentConfig(self, output_dimensionality=None):
        return SimpleNamespace(output_dimensionality=output_dimensionality)

    def HttpOptions(self, timeout=None):
        return SimpleNamespace(timeout=timeout)


@pytest.fixture
def fake_genai(monkeypatch):
    fake = FakeGenAIModule()
    monkeypatch.setitem(sys.modules, "google.genai", fake)
    monkeypatch.setitem(sys.modules, "google.genai.types", FakeTypesModule())
    monkeypatch.setitem(sys.modules, "google", sys.modules.get("google", types.ModuleType("google")))
    return fake


@pytest.fixture(autouse=True)
def clean_gemini_env(monkeypatch):
    for var in ("GOOGLE_API_KEYS", "GEMINI_API_KEY", "GEMINI_EMBEDDING_DIM"):
        monkeypatch.delenv(var, raising=False)


def test_round_robin_cycles_through_all_keys(monkeypatch, fake_genai):
    monkeypatch.setenv("GOOGLE_API_KEYS", "key1,key2,key3")
    from shared.embedder import GeminiProvider

    provider = GeminiProvider()
    for _ in range(4):
        provider.embed_query("hello")

    used_keys = [key for key, _ in fake_genai.calls]
    assert used_keys == ["key1", "key2", "key3", "key1"]  # wraps around


def test_retries_next_key_on_failure(monkeypatch, fake_genai):
    monkeypatch.setenv("GOOGLE_API_KEYS", "bad-key,good-key")
    fake_genai.fail_keys = {"bad-key"}
    from shared.embedder import GeminiProvider

    provider = GeminiProvider()
    result = provider.embed_query("hello")

    assert result == [0.1, 0.2, 0.3]
    assert [key for key, _ in fake_genai.calls] == ["bad-key", "good-key"]


def test_raises_once_every_key_has_failed(monkeypatch, fake_genai):
    monkeypatch.setenv("GOOGLE_API_KEYS", "key1,key2")
    fake_genai.fail_keys = {"key1", "key2"}
    from shared.embedder import GeminiProvider

    provider = GeminiProvider()
    with pytest.raises(RuntimeError, match="All 2 Gemini API key"):
        provider.embed_query("hello")


def test_requires_at_least_one_key(monkeypatch, fake_genai):
    from shared.embedder import GeminiProvider

    with pytest.raises(ValueError):
        GeminiProvider()


def test_falls_back_to_single_gemini_api_key(monkeypatch, fake_genai):
    monkeypatch.setenv("GEMINI_API_KEY", "solo-key")
    from shared.embedder import GeminiProvider

    provider = GeminiProvider()
    provider.embed_query("hello")

    assert fake_genai.calls[0][0] == "solo-key"


def test_default_model_and_dimension(monkeypatch, fake_genai):
    monkeypatch.setenv("GOOGLE_API_KEYS", "key1")
    from shared.embedder import _MODEL, GeminiProvider

    provider = GeminiProvider()
    provider.embed_query("hello")

    assert provider.get_vector_size() == 3072
    assert _MODEL == "gemini-embedding-2"


def test_custom_dimension_is_respected(monkeypatch, fake_genai):
    monkeypatch.setenv("GOOGLE_API_KEYS", "key1")
    monkeypatch.setenv("GEMINI_EMBEDDING_DIM", "768")
    from shared.embedder import GeminiProvider

    provider = GeminiProvider()

    assert provider.get_vector_size() == 768


def test_embed_query_uses_search_task_prefix(monkeypatch, fake_genai):
    monkeypatch.setenv("GOOGLE_API_KEYS", "key1")
    from shared.embedder import GeminiProvider

    provider = GeminiProvider()
    provider.embed_query("what is outline?")

    assert fake_genai.calls[0][1] == "task: search result | query: what is outline?"


def test_embed_passages_uses_document_prefix_with_title(monkeypatch, fake_genai):
    monkeypatch.setenv("GOOGLE_API_KEYS", "key1")
    from shared.embedder import GeminiProvider

    provider = GeminiProvider()
    provider.embed_passages(["chunk one", "chunk two"], title="My Doc")

    assert [content for _, content in fake_genai.calls] == [
        "title: My Doc | text: chunk one",
        "title: My Doc | text: chunk two",
    ]


def test_embed_passages_defaults_title_to_none(monkeypatch, fake_genai):
    monkeypatch.setenv("GOOGLE_API_KEYS", "key1")
    from shared.embedder import GeminiProvider

    provider = GeminiProvider()
    provider.embed_passages(["chunk one"])

    assert fake_genai.calls[0][1] == "title: none | text: chunk one"


def test_embed_passages_preserves_order(monkeypatch, fake_genai):
    monkeypatch.setenv("GOOGLE_API_KEYS", "key1,key2")
    from shared.embedder import GeminiProvider

    provider = GeminiProvider()
    result = provider.embed_passages(["a", "b", "c"], title="T")

    assert len(result) == 3
    assert [content for _, content in fake_genai.calls] == [
        "title: T | text: a",
        "title: T | text: b",
        "title: T | text: c",
    ]
