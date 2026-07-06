import sys
import types

import pytest


class FakeGenAI(types.ModuleType):
    """Stand-in for google.generativeai, controllable per-test."""

    def __init__(self):
        super().__init__("google.generativeai")
        self.calls: list[tuple[str, str, str]] = []
        self.fail_keys: set[str] = set()
        self._current_key: str | None = None

    def configure(self, api_key):
        self._current_key = api_key

    def embed_content(self, model, content, task_type, output_dimensionality=None):
        self.calls.append((self._current_key, content, task_type))
        if self._current_key in self.fail_keys:
            raise RuntimeError("429 rate limited")
        return {"embedding": [0.1, 0.2, 0.3]}


@pytest.fixture
def fake_genai(monkeypatch):
    fake = FakeGenAI()
    monkeypatch.setitem(sys.modules, "google.generativeai", fake)
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

    used_keys = [key for key, _, _ in fake_genai.calls]
    assert used_keys == ["key1", "key2", "key3", "key1"]  # wraps around


def test_retries_next_key_on_failure(monkeypatch, fake_genai):
    monkeypatch.setenv("GOOGLE_API_KEYS", "bad-key,good-key")
    fake_genai.fail_keys = {"bad-key"}
    from shared.embedder import GeminiProvider

    provider = GeminiProvider()
    result = provider.embed_query("hello")

    assert result == [0.1, 0.2, 0.3]
    assert [key for key, _, _ in fake_genai.calls] == ["bad-key", "good-key"]


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
    from shared.embedder import _MODEL_PATH, GeminiProvider

    provider = GeminiProvider()
    provider.embed_query("hello")

    assert provider.get_vector_size() == 3072
    assert _MODEL_PATH == "models/gemini-embedding-002"


def test_custom_dimension_is_respected(monkeypatch, fake_genai):
    monkeypatch.setenv("GOOGLE_API_KEYS", "key1")
    monkeypatch.setenv("GEMINI_EMBEDDING_DIM", "768")
    from shared.embedder import GeminiProvider

    provider = GeminiProvider()

    assert provider.get_vector_size() == 768


def test_embed_passages_preserves_order(monkeypatch, fake_genai):
    monkeypatch.setenv("GOOGLE_API_KEYS", "key1,key2")
    from shared.embedder import GeminiProvider

    provider = GeminiProvider()
    result = provider.embed_passages(["a", "b", "c"])

    assert len(result) == 3
    assert [content for _, content, task_type in fake_genai.calls] == ["a", "b", "c"]
    assert all(task_type == "RETRIEVAL_DOCUMENT" for _, _, task_type in fake_genai.calls)
