from bot.format import format_dual, split_telegram


def test_short_text_stays_one_chunk() -> None:
    assert split_telegram("привет") == ["привет"]


def test_empty_becomes_ellipsis() -> None:
    assert split_telegram("   ") == ["…"]


def test_splits_on_paragraph() -> None:
    text = ("слово " * 50 + "\n\n") * 20
    chunks = split_telegram(text, limit=200)
    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for chunk in chunks)
    assert "".join(part.replace("\n", "") for part in chunks).count("слово") >= 100


def test_dual_format_has_both_heads() -> None:
    out = format_dual("один", "два")
    assert "🧠 ChatGPT" in out
    assert "⚡ Grok" in out
    assert "один" in out
    assert "два" in out
