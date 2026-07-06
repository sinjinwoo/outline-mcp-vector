import re

CHUNK_SIZE = int(800 * 4)   # ~800 tokens in chars (1 token ≈ 4 chars)
CHUNK_OVERLAP = int(120 * 4)  # ~120 tokens in chars

_HEADER_RE = re.compile(r"^#{1,3}\s+.+$", re.MULTILINE)


def chunk_markdown(text: str) -> list[str]:
    """Split markdown into search-optimised chunks.

    Strategy:
    1. Split on H1/H2/H3 headers first.
    2. If a section still exceeds CHUNK_SIZE, split further by paragraphs
       with overlap.
    """
    if not text.strip():
        return []

    sections = _split_by_headers(text)

    chunks: list[str] = []
    for section in sections:
        if len(section) <= CHUNK_SIZE:
            if section.strip():
                chunks.append(section.strip())
        else:
            chunks.extend(_split_by_size(section))

    return [c for c in chunks if c.strip()]


def _split_by_headers(text: str) -> list[str]:
    """Partition text at every H1/H2/H3 boundary."""
    boundaries = [m.start() for m in _HEADER_RE.finditer(text)]
    if not boundaries:
        return [text]

    sections: list[str] = []
    if boundaries[0] > 0:
        sections.append(text[: boundaries[0]])

    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(text)
        sections.append(text[start:end])

    return sections


def _split_by_size(text: str) -> list[str]:
    """Sliding-window split with paragraph-boundary preference."""
    chunks: list[str] = []
    start = 0

    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))

        if end < len(text):
            # Prefer breaking at a paragraph boundary
            para_break = text.rfind("\n\n", start, end)
            if para_break > start:
                end = para_break
            else:
                line_break = text.rfind("\n", start, end)
                if line_break > start:
                    end = line_break

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        next_start = end - CHUNK_OVERLAP
        if next_start <= start:
            next_start = end  # guard against infinite loop

        start = next_start

    return chunks
