from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "assistant.db"

GPT = "gpt"
GROK = "grok"
BOTH = "both"
MODELS = (GPT, GROK, BOTH)


@dataclass
class Config:
    bot_token: str
    openai_api_key: str = ""
    xai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    xai_model: str = "grok-4.6"
    openai_base_url: str = "https://api.openai.com/v1"
    xai_base_url: str = "https://api.x.ai/v1"
    db_path: Path = DEFAULT_DB
    history_limit: int = 24
    max_tokens: int = 2048
    bot_username: str = field(default="")

    def key_for(self, model: str) -> str:
        if model == GPT:
            return self.openai_api_key
        if model == GROK:
            return self.xai_api_key
        return self.openai_api_key or self.xai_api_key


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Создай .env из .env.example и вставь токен от @BotFather."
        )
    return Config(
        bot_token=token,
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        xai_api_key=os.getenv("XAI_API_KEY", "").strip(),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
        xai_model=os.getenv("XAI_MODEL", "grok-4.6").strip() or "grok-4.6",
        openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip(),
        xai_base_url=os.getenv("XAI_BASE_URL", "https://api.x.ai/v1").strip(),
        db_path=Path(os.getenv("DB_PATH", str(DEFAULT_DB))),
        history_limit=int(os.getenv("HISTORY_LIMIT", "24")),
        max_tokens=int(os.getenv("MAX_TOKENS", "2048")),
    )
