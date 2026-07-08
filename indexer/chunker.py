import re

CHUNK_SIZE = int(800 * 4)   # ~800 tokens in chars (1 token ≈ 4 chars)
CHUNK_OVERLAP = int(120 * 4)  # ~120 tokens in chars
MIN_CHUNK_SIZE = int(200 * 4)  # ~200 tokens — sections smaller than this get merged with a neighbor

_HEADER_RE = re.compile(r"^#{1,3}\s+.+$", re.MULTILINE)
_LIST_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])\s+\S")
_TABLE_ROW_RE = re.compile(r"^[ \t]*\|.*\|[ \t]*$")


def chunk_markdown(text: str) -> list[str]:
    """Split markdown into search-optimised chunks.

    Strategy:
    1. Split on H1/H2/H3 headers first.
    2. If a section still exceeds CHUNK_SIZE, split further by paragraphs
       with overlap (never cutting a list/table block in half).
    3. Merge any resulting chunks under MIN_CHUNK_SIZE into a neighbor, so a
       header-heavy document (many short H2/H3 subsections) doesn't
       fragment into near-contentless one-sentence vectors.
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

    return _merge_small_chunks([c for c in chunks if c.strip()])


def _merge_small_chunks(chunks: list[str]) -> list[str]:
    """Merge chunks under MIN_CHUNK_SIZE into the running buffer. Only the
    buffer's own size gates merging (not the incoming chunk's), so an
    already-adequately-sized chunk is never forced into a neighbor just
    because the neighbor happens to be small."""
    merged: list[str] = []
    buffer = ""
    for chunk in chunks:
        if not buffer:
            buffer = chunk
            continue
        if len(buffer) < MIN_CHUNK_SIZE:
            candidate = f"{buffer}\n\n{chunk}"
            if len(candidate) <= CHUNK_SIZE:
                buffer = candidate
                continue
        merged.append(buffer)
        buffer = chunk
    if buffer:
        merged.append(buffer)
    return merged


def _split_by_headers(text: str) -> list[str]:
    """Partition text at every H1/H2/H3 boundary.

    Header-like lines inside fenced code blocks (```...```) are ignored so
    code blocks are never split apart (e.g. a Python "# comment" or a
    Markdown snippet must not be treated as a heading).
    """
    boundaries: list[int] = []
    for m in _HEADER_RE.finditer(text):
        fences_before = text.count("```", 0, m.start())
        if fences_before % 2 == 0:  # even → not inside an open code fence
            boundaries.append(m.start())

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
    blocks = _find_atomic_blocks(text)
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

        end = _snap_out_of_atomic_block(end, start, blocks)

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        next_start = end - CHUNK_OVERLAP
        if next_start <= start:
            next_start = end  # guard against infinite loop

        start = next_start

    return chunks


def _find_atomic_blocks(text: str) -> list[tuple[int, int]]:
    """Character spans of contiguous list-item or table-row lines. These must
    never be split mid-block — cutting a table between its header and body
    rows, or a list between items, produces a chunk fragment that no longer
    reads as a coherent unit."""
    blocks: list[tuple[int, int]] = []
    block_start: int | None = None
    pos = 0
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        is_atomic = bool(_LIST_ITEM_RE.match(stripped) or _TABLE_ROW_RE.match(stripped))
        if is_atomic and block_start is None:
            block_start = pos
        elif not is_atomic and block_start is not None:
            blocks.append((block_start, pos))
            block_start = None
        pos += len(line)
    if block_start is not None:
        blocks.append((block_start, pos))
    return blocks


def _snap_out_of_atomic_block(end: int, start: int, blocks: list[tuple[int, int]]) -> int:
    """If `end` lands inside a list/table block, push it to the block's edge
    — before the block if that still makes progress, otherwise past it
    (accepting some CHUNK_SIZE overshoot rather than splitting the block)."""
    for block_start, block_end in blocks:
        if block_start < end < block_end:
            return block_start if block_start > start else block_end
    return end
