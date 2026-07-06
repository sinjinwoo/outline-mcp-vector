from indexer.chunker import chunk_markdown


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
    text = "# Title\n\nIntro.\n\n## Second heading\n\nSecond section body.\n"

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
