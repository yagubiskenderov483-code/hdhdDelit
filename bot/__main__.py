from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot.config import load_config
from bot.db import Database
from bot.handlers import build_router, send_evening_reminders

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("ogonyok")


async def main() -> None:
    config = load_config()
    db = Database(config.db_path)
    await db.connect()

    bot = Bot(token=config.bot_token)
    me = await bot.get_me()
    config.bot_username = me.username or ""
    logger.info("Бот @%s запущен", config.bot_username)

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Запуск и ссылка-приглашение"),
            BotCommand(command="friends", description="Друзья и огоньки"),
            BotCommand(command="add", description="Добавить друга"),
            BotCommand(command="top", description="Топ огоньков"),
            BotCommand(command="help", description="Как работает огонёк"),
            BotCommand(command="cancel", description="Отмена"),
        ]
    )

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(build_router(db, config))

    scheduler = AsyncIOScheduler(timezone=config.tz)
    scheduler.add_job(
        send_evening_reminders,
        "cron",
        hour=config.remind_hour,
        minute=0,
        kwargs={"bot": bot, "db": db, "tz": config.tz},
        id="evening_fire_reminder",
        replace_existing=True,
    )
    scheduler.start()

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await db.close()
        await bot.session.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
