from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "ogonyok.db"


@dataclass
class Config:
    bot_token: str
    db_path: Path = DEFAULT_DB
    timezone_name: str = "Europe/Moscow"
    remind_hour: int = 20
    bot_username: str = field(default="")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)


def load_config() -> Config:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Создай .env из .env.example и вставь токен от @BotFather."
        )
    db_path = Path(os.getenv("DB_PATH", str(DEFAULT_DB)))
    timezone_name = os.getenv("TIMEZONE", "Europe/Moscow").strip() or "Europe/Moscow"
    remind_hour = int(os.getenv("REMIND_HOUR", "20"))
    return Config(
        bot_token=token,
        db_path=db_path,
        timezone_name=timezone_name,
        remind_hour=remind_hour,
    )
