from __future__ import annotations

import asyncio
import base64
import logging
import tempfile
import time
from pathlib import Path

from aiogram import F, Router
from aiogram.enums import ChatAction, ChatType, ContentType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot.ai import (
    AIError,
    LLM,
    MissingKeyError,
    SYSTEM_PROMPTS,
    pack_messages,
    require_llm,
    transcribe_voice,
)
from bot.config import BOTH, GPT, GROK, Config
from bot.db import Database
from bot.format import format_dual, model_title, split_telegram
from bot.keyboards import (
    BTN_BOTH,
    BTN_GPT,
    BTN_GROK,
    BTN_HELP,
    BTN_RESET,
    main_keyboard,
)

logger = logging.getLogger("assistant")

HELP = (
    "Я ИИ-помощник в Telegram: можно болтать с ChatGPT, с Grok или с обоими сразу.\n\n"
    "🧠 ChatGPT — спокойный и чёткий\n"
    "⚡ Grok — прямее и с характером\n"
    "🔥 Оба сразу — два ответа на один вопрос\n"
    "🧹 Новый чат — стереть память\n\n"
    "Можно кидать текст, голосовые и фото с подписью.\n"
    "Нужны ключи API: OPENAI_API_KEY и XAI_API_KEY в .env."
)

WELCOME = (
    "Йо. Я не огонёк, я мозг.\n\n"
    "Выбери, с кем болтать — ChatGPT, Grok, или оба сразу. "
    "Потом просто пиши, как в обычном чате."
)


def build_router(db: Database, config: Config, llms: dict[str, LLM]) -> Router:
    router = Router()
    locks: dict[int, asyncio.Lock] = {}

    def lock_for(user_id: int) -> asyncio.Lock:
        if user_id not in locks:
            locks[user_id] = asyncio.Lock()
        return locks[user_id]

    async def ensure_user(message: Message) -> tuple[int, str]:
        user = message.from_user
        assert user is not None
        model = await db.upsert_user(user.id, user.username, user.first_name or "")
        return user.id, model

    async def send_long(message: Message, text: str) -> None:
        for chunk in split_telegram(text):
            await message.answer(chunk, reply_markup=main_keyboard())

    async def stream_to_message(status: Message, llm: LLM, messages: list[dict]) -> str:
        last_edit = 0.0
        last_shown = ""

        async def on_delta(full: str) -> None:
            nonlocal last_edit, last_shown
            now = time.monotonic()
            if now - last_edit < 0.45:
                return
            preview = split_telegram(full)[0]
            if preview == last_shown:
                return
            last_edit = now
            last_shown = preview
            try:
                await status.edit_text(preview)
            except TelegramBadRequest:
                pass

        text = await llm.complete(
            messages,
            max_tokens=config.max_tokens,
            on_delta=on_delta,
        )
        return text or "Пустой ответ. Спроси ещё раз иначе."

    async def answer_with_model(
        message: Message,
        user_id: int,
        model: str,
        user_text: str,
        user_content,
    ) -> None:
        history = await db.history(user_id, config.history_limit)
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

        if model == BOTH:
            status = await message.answer("🧠 ChatGPT и ⚡ Grok думают…")
            async def one(name: str) -> str:
                llm = require_llm(llms, name)
                packed = pack_messages(SYSTEM_PROMPTS[name], history, user_content)
                return await llm.complete(packed, max_tokens=config.max_tokens)

            gpt_task = asyncio.create_task(one(GPT))
            grok_task = asyncio.create_task(one(GROK))
            results = await asyncio.gather(gpt_task, grok_task, return_exceptions=True)

            def unwrap(item, label: str) -> str:
                if isinstance(item, MissingKeyError):
                    return str(item)
                if isinstance(item, AIError):
                    return str(item)
                if isinstance(item, Exception):
                    logger.exception("%s failed", label)
                    return f"{label}: что-то сломалось."
                return str(item)

            reply = format_dual(unwrap(results[0], "ChatGPT"), unwrap(results[1], "Grok"))
            await status.delete()
            await send_long(message, reply)
            await db.add_message(user_id, "user", user_text)
            await db.add_message(user_id, "assistant", reply)
            await db.trim_history(user_id, config.history_limit * 2)
            return

        llm = require_llm(llms, model)
        packed = pack_messages(SYSTEM_PROMPTS[model], history, user_content)
        status = await message.answer(f"{model_title(model)} думает…")
        reply = await stream_to_message(status, llm, packed)
        chunks = split_telegram(reply)
        try:
            await status.edit_text(chunks[0])
        except TelegramBadRequest:
            await send_long(message, reply)
            chunks = []
        for extra in chunks[1:]:
            await message.answer(extra, reply_markup=main_keyboard())
        await db.add_message(user_id, "user", user_text)
        await db.add_message(user_id, "assistant", reply)
        await db.trim_history(user_id, config.history_limit * 2)

    async def handle_prompt(message: Message, user_text: str, user_content) -> None:
        user_id, model = await ensure_user(message)
        async with lock_for(user_id):
            try:
                await answer_with_model(message, user_id, model, user_text, user_content)
            except MissingKeyError as exc:
                await message.answer(str(exc), reply_markup=main_keyboard())
            except AIError as exc:
                await message.answer(str(exc), reply_markup=main_keyboard())
            except Exception:
                logger.exception("chat failed")
                await message.answer(
                    "Сбой на стороне модели. Попробуй ещё раз.",
                    reply_markup=main_keyboard(),
                )

    @router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
    async def cmd_start(message: Message) -> None:
        user_id, model = await ensure_user(message)
        await message.answer(
            f"{WELCOME}\n\nСейчас: {model_title(model)}",
            reply_markup=main_keyboard(),
        )

    @router.message(Command("help"), F.chat.type == ChatType.PRIVATE)
    @router.message(F.text == BTN_HELP)
    async def cmd_help(message: Message) -> None:
        await ensure_user(message)
        await message.answer(HELP, reply_markup=main_keyboard())

    @router.message(Command("gpt"), F.chat.type == ChatType.PRIVATE)
    @router.message(F.text == BTN_GPT)
    async def cmd_gpt(message: Message) -> None:
        user_id, _ = await ensure_user(message)
        await db.set_model(user_id, GPT)
        extra = "" if config.openai_api_key else "\n\nКлюча OpenAI пока нет — добавь OPENAI_API_KEY."
        await message.answer(
            f"Ок, дальше отвечает ChatGPT.{extra}",
            reply_markup=main_keyboard(),
        )

    @router.message(Command("grok"), F.chat.type == ChatType.PRIVATE)
    @router.message(F.text == BTN_GROK)
    async def cmd_grok(message: Message) -> None:
        user_id, _ = await ensure_user(message)
        await db.set_model(user_id, GROK)
        extra = "" if config.xai_api_key else "\n\nКлюча xAI пока нет — добавь XAI_API_KEY."
        await message.answer(
            f"Ок, дальше отвечает Grok.{extra}",
            reply_markup=main_keyboard(),
        )

    @router.message(Command("both"), F.chat.type == ChatType.PRIVATE)
    @router.message(F.text == BTN_BOTH)
    async def cmd_both(message: Message) -> None:
        user_id, _ = await ensure_user(message)
        await db.set_model(user_id, BOTH)
        await message.answer(
            "Режим дуэли: на каждый вопрос ответят и ChatGPT, и Grok.",
            reply_markup=main_keyboard(),
        )

    @router.message(Command("reset"), F.chat.type == ChatType.PRIVATE)
    @router.message(Command("new"), F.chat.type == ChatType.PRIVATE)
    @router.message(F.text == BTN_RESET)
    async def cmd_reset(message: Message) -> None:
        user_id, _ = await ensure_user(message)
        await db.clear_history(user_id)
        await message.answer("Память стёрта. Начинаем с чистого листа.", reply_markup=main_keyboard())

    @router.message(F.chat.type == ChatType.PRIVATE, F.content_type == ContentType.VOICE)
    async def on_voice(message: Message) -> None:
        if message.voice is None:
            return
        await ensure_user(message)
        await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        file = await message.bot.get_file(message.voice.file_id)
        if file.file_path is None:
            await message.answer("Не смог скачать голосовое.")
            return
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "voice.ogg"
            await message.bot.download_file(file.file_path, destination=path)
            try:
                text = await transcribe_voice(config, str(path))
            except MissingKeyError as exc:
                await message.answer(
                    f"{exc}\nГолосовые расшифровывает Whisper, для этого нужен ключ OpenAI.",
                    reply_markup=main_keyboard(),
                )
                return
        if not text:
            await message.answer("Не разобрал голосовое. Напиши текстом.")
            return
        await message.answer(f"🎤 {text}")
        await handle_prompt(message, text, text)

    @router.message(F.chat.type == ChatType.PRIVATE, F.photo)
    async def on_photo(message: Message) -> None:
        photo = message.photo[-1]
        caption = (message.caption or "Что на фото? Опиши и помоги.").strip()
        file = await message.bot.get_file(photo.file_id)
        if file.file_path is None:
            await message.answer("Не смог скачать фото.")
            return
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "photo.jpg"
            await message.bot.download_file(file.file_path, destination=path)
            b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        content = [
            {"type": "text", "text": caption},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]
        await handle_prompt(message, f"[фото] {caption}", content)

    @router.message(F.chat.type == ChatType.PRIVATE, F.text)
    async def on_text(message: Message) -> None:
        text = (message.text or "").strip()
        if not text:
            return
        await handle_prompt(message, text, text)

    @router.message(F.chat.type == ChatType.PRIVATE)
    async def fallback(message: Message) -> None:
        await message.answer(
            "Кинь текст, голосовое или фото — остальное пока не ем.",
            reply_markup=main_keyboard(),
        )

    return router
