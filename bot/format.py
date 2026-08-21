from __future__ import annotations

TELEGRAM_LIMIT = 4000


def split_telegram(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    text = (text or "").strip()
    if not text:
        return ["…"]
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            chunks.append(rest)
            break
        window = rest[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 3:
            cut = window.rfind("\n")
        if cut < limit // 3:
            cut = window.rfind(" ")
        if cut < limit // 3:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return chunks or ["…"]


def format_dual(gpt_text: str, grok_text: str) -> str:
    return (
        f"🧠 ChatGPT\n{gpt_text.strip() or '—'}\n\n"
        f"⚡ Grok\n{grok_text.strip() or '—'}"
    )


def model_title(model: str) -> str:
    return {
        "gpt": "🧠 ChatGPT",
        "grok": "⚡ Grok",
        "both": "🔥 Оба сразу",
    }.get(model, model)
