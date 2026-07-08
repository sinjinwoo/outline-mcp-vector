from indexer.chunker import CHUNK_SIZE, chunk_markdown


def test_code_block_with_comment_lines_stays_intact():
    # Regression test: lines like "# comment" or "## comment" inside a
    # fenced code block used to be mistaken for markdown headings and
    # split the code block apart.
    text = (
        "# Title\n\n"
        "Some intro text.\n\n"
        "```python\n"
        "# This is a comment\n"
        "def foo():\n"
        "    ## another comment\n"
        "    return 1\n"
        "```\n\n"
        "More text after code block.\n"
    )

    chunks = chunk_markdown(text)

    assert len(chunks) == 1
    assert "```python" in chunks[0]
    assert "def foo():" in chunks[0]
    assert chunks[0].count("```") == 2  # opening and closing fence both present


def test_real_headings_outside_code_blocks_still_split():
    # Padded well past MIN_CHUNK_SIZE so the small-chunk merge pass (which
    # combines short neighboring sections) doesn't fold these back together —
    # this test is specifically about header-boundary detection.
    filler = "word " * 250
    text = f"# Title\n\n{filler}\n\n## Second heading\n\n{filler}\n"

    chunks = chunk_markdown(text)

    assert len(chunks) == 2
    assert chunks[0].startswith("# Title")
    assert chunks[1].startswith("## Second heading")


def test_empty_text_returns_no_chunks():
    assert chunk_markdown("") == []
    assert chunk_markdown("   \n  ") == []


def test_long_section_without_headings_is_split_with_overlap():
    long_paragraph = "word " * 2000  # comfortably exceeds CHUNK_SIZE
    chunks = chunk_markdown(f"# Title\n\n{long_paragraph}")

    assert len(chunks) > 1
    # Consecutive chunks should overlap rather than lose content at the boundary
    assert chunks[0][-20:] in chunks[0] + chunks[1]


def test_short_header_sections_are_merged_into_fewer_chunks():
    # A document with many one-sentence H3 subsections used to fragment into
    # one chunk per header — each merged chunk should now span several of
    # the original sections instead.
    text = "\n\n".join(f"### Section {i}\n\nOne short sentence." for i in range(20))

    chunks = chunk_markdown(text)

    assert len(chunks) < 20
    assert any(chunk.count("### Section") > 1 for chunk in chunks)


def test_adequately_sized_sections_are_not_merged():
    # Sections already comfortably over MIN_CHUNK_SIZE must stay standalone
    # even though a smaller neighbor would technically still fit alongside them.
    filler = "word " * 250
    text = f"## A\n\n{filler}\n\n## B\n\n{filler}\n"

    chunks = chunk_markdown(text)

    assert len(chunks) == 2
    assert chunks[0].startswith("## A")
    assert chunks[1].startswith("## B")


def test_large_table_is_not_split_across_chunks():
    header_line = "# Title\n"
    table_header = "| Col A | Col B |\n| --- | --- |\n"
    rows = "".join(f"| value {i} | value {i} |\n" for i in range(160))
    text = header_line + table_header + rows
    assert len(text) > CHUNK_SIZE  # must actually exercise _split_by_size

    chunks = chunk_markdown(text)

    assert any("Col A" in c and "value 159" in c for c in chunks)


def test_long_list_is_not_split_across_chunks():
    header_line = "# Title\n"
    items = "".join(f"- item {i}: some descriptive filler text here\n" for i in range(150))
    text = header_line + items
    assert len(text) > CHUNK_SIZE  # must actually exercise _split_by_size

    chunks = chunk_markdown(text)

    assert any("item 0:" in c and "item 149:" in c for c in chunks)
