from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from bot.ai import build_llms
from bot.config import load_config
from bot.db import Database
from bot.handlers import build_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("assistant")


async def main() -> None:
    config = load_config()
    db = Database(config.db_path)
    await db.connect()
    llms = build_llms(config)
    logger.info(
        "Модели: ChatGPT=%s Grok=%s",
        "on" if "gpt" in llms else "no-key",
        "on" if "grok" in llms else "no-key",
    )

    bot = Bot(token=config.bot_token)
    me = await bot.get_me()
    config.bot_username = me.username or ""
    logger.info("Бот @%s запущен", config.bot_username)

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Старт"),
            BotCommand(command="gpt", description="Режим ChatGPT"),
            BotCommand(command="grok", description="Режим Grok"),
            BotCommand(command="both", description="Спросить обоих"),
            BotCommand(command="reset", description="Новый чат"),
            BotCommand(command="help", description="Помощь"),
        ]
    )

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(build_router(db, config, llms))

    try:
        await dp.start_polling(bot)
    finally:
        await db.close()
        await bot.session.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
