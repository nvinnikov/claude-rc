from tgclaude.render import chunks


def test_chunks_returns_single_piece_when_short():
    assert chunks("hello") == ["hello"]


def test_chunks_splits_on_newline_boundary():
    text = "\n".join(["x" * 100] * 60)

    parts = chunks(text, limit=1000)

    assert len(parts) > 1
    assert all(len(p) <= 1000 for p in parts)
    # Each boundary consumes exactly one separator newline; re-inserting one
    # newline per boundary must reconstruct the original with no lost chars.
    assert "\n".join(parts) == text


def test_chunks_hard_splits_when_no_newline():
    parts = chunks("y" * 2500, limit=1000)

    assert [len(p) for p in parts] == [1000, 1000, 500]


def test_chunks_preserves_blank_line_at_boundary():
    text = "A" * 990 + "\n\n" + "B" * 500

    parts = chunks(text, limit=992)

    assert "".join(parts).count("A") == 990
    assert "".join(parts).count("B") == 500
    # The blank line's newlines must survive (only one consumed as separator).
    assert "\n".join(parts) == text
