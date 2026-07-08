import os
import re

import httpx

from shared.embedder import caption_image

# Same fallback pattern as indexer/tasks.py: OUTLINE_API_URL for co-located
# internal networking, falling back to the public OUTLINE_BASE_URL.
OUTLINE_API_URL = os.getenv("OUTLINE_API_URL") or os.getenv("OUTLINE_BASE_URL", "")
OUTLINE_API_KEY = os.getenv("OUTLINE_API_KEY", "")
_HEADERS = {"Authorization": f"Bearer {OUTLINE_API_KEY}"}

_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


def inline_image_captions(text: str) -> str:
    """Replace every markdown image tag with a Gemma-generated caption so
    chunking/embedding sees real descriptive content instead of a bare
    attachment URL, which carries no retrievable meaning and wastes chunk
    budget."""
    return _IMAGE_RE.sub(_replace, text)


def _replace(match: re.Match) -> str:
    alt, url = match.group(1), match.group(2)
    try:
        image_bytes, mime_type = _fetch_image(url)
        caption = caption_image(image_bytes, mime_type)
        return f"[image: {caption}]"
    except Exception as exc:
        # A single bad/unreachable image must not fail the whole document's
        # indexing — fall back to whatever alt text is there, or drop the
        # tag entirely.
        print(f"[image_captions] Failed to caption {url}: {exc}")
        return f"[image: {alt}]" if alt else ""


def _fetch_image(url: str) -> tuple[bytes, str]:
    if url.startswith("http://") or url.startswith("https://"):
        # Only attach the Outline auth header to same-origin URLs — never
        # leak the API key to a third-party host (e.g. an externally hosted image).
        headers = _HEADERS if OUTLINE_API_URL and url.startswith(OUTLINE_API_URL) else {}
        resp = httpx.get(url, headers=headers, timeout=30.0)
    else:
        resp = httpx.get(f"{OUTLINE_API_URL.rstrip('/')}{url}", headers=_HEADERS, timeout=30.0)
    resp.raise_for_status()
    mime_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
    return resp.content, mime_type
