import httpx
import pytest

import indexer.image_captions as image_captions


@pytest.fixture(autouse=True)
def _set_outline_env(monkeypatch):
    monkeypatch.setattr(image_captions, "OUTLINE_API_URL", "https://outline.example.com")
    monkeypatch.setattr(image_captions, "OUTLINE_API_KEY", "test-key")
    monkeypatch.setattr(image_captions, "_HEADERS", {"Authorization": "Bearer test-key"})


def test_image_tag_is_replaced_with_caption(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        assert url == "https://outline.example.com/api/attachments.redirect?id=abc"
        return httpx.Response(
            200,
            content=b"fake-bytes",
            headers={"content-type": "image/png"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(image_captions.httpx, "get", fake_get)
    monkeypatch.setattr(image_captions, "caption_image", lambda data, mime: "a dashboard screenshot")

    text = "Before.\n\n![Dashboard](/api/attachments.redirect?id=abc)\n\nAfter."
    result = image_captions.inline_image_captions(text)

    assert "[image: a dashboard screenshot]" in result
    assert "attachments.redirect" not in result


def test_same_origin_url_gets_auth_header_external_does_not(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured[url] = headers
        return httpx.Response(
            200,
            content=b"fake-bytes",
            headers={"content-type": "image/jpeg"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(image_captions.httpx, "get", fake_get)
    monkeypatch.setattr(image_captions, "caption_image", lambda data, mime: "caption")

    text = (
        "![a](https://outline.example.com/api/attachments.redirect?id=1)\n"
        "![b](https://cdn.other.com/image.png)\n"
    )
    image_captions.inline_image_captions(text)

    assert captured["https://outline.example.com/api/attachments.redirect?id=1"] == {
        "Authorization": "Bearer test-key"
    }
    assert captured["https://cdn.other.com/image.png"] == {}


def test_caption_failure_falls_back_to_alt_text(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        raise httpx.ConnectError("boom", request=httpx.Request("GET", url))

    monkeypatch.setattr(image_captions.httpx, "get", fake_get)

    text = "![a helpful alt text](/api/attachments.redirect?id=1)"
    result = image_captions.inline_image_captions(text)

    assert result == "[image: a helpful alt text]"


def test_caption_failure_without_alt_text_drops_tag(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        raise httpx.ConnectError("boom", request=httpx.Request("GET", url))

    monkeypatch.setattr(image_captions.httpx, "get", fake_get)

    text = "Before ![](/api/attachments.redirect?id=1) after."
    result = image_captions.inline_image_captions(text)

    assert result == "Before  after."


def test_downstream_failure_does_not_affect_other_images(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        if "bad" in url:
            raise httpx.ConnectError("boom", request=httpx.Request("GET", url))
        return httpx.Response(
            200,
            content=b"fake-bytes",
            headers={"content-type": "image/png"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(image_captions.httpx, "get", fake_get)
    monkeypatch.setattr(image_captions, "caption_image", lambda data, mime: "good caption")

    text = "![good alt](/api/attachments.redirect?id=good)\n![bad alt](/api/attachments.redirect?id=bad)"
    result = image_captions.inline_image_captions(text)

    assert "[image: good caption]" in result
    assert "[image: bad alt]" in result
