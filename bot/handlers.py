from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.config import Config
from bot.db import Database, FriendRow, User
from bot.keyboards import (
    FriendCB,
    chat_actions_keyboard,
    chat_keyboard,
    friends_keyboard,
    incoming_message_keyboard,
    main_keyboard,
    remind_keyboard,
    request_keyboard,
)
from bot.streak import fire_status, register_write
from bot import texts


class Form(StatesGroup):
    waiting_username = State()
    chatting = State()


def build_router(db: Database, config: Config) -> Router:
    router = Router()
    tz = config.tz

    def today() -> date:
        return datetime.now(tz).date()

    async def ensure_user(message: Message) -> User:
        user = message.from_user
        assert user is not None
        return await db.upsert_user(user.id, user.username, user.first_name or "")

    async def open_chat(
        *,
        user_id: int,
        partner: User,
        row: FriendRow,
        message: Message | None = None,
        callback: CallbackQuery | None = None,
        state: FSMContext,
    ) -> None:
        await db.set_partner(user_id, partner.user_id)
        await state.set_state(Form.chatting)
        await state.update_data(partner_id=partner.user_id)
        text = texts.chat_opened(
            partner,
            texts.status_for(row, user_id, today()),
        )
        markup = chat_keyboard()
        extra = chat_actions_keyboard(partner.user_id)
        if callback and callback.message:
            await callback.message.answer(text, reply_markup=markup)
            await callback.message.answer("Действия:", reply_markup=extra)
            await callback.answer()
        elif message:
            await message.answer(text, reply_markup=markup)
            await message.answer("Действия:", reply_markup=extra)

    async def leave_chat(message: Message, state: FSMContext) -> None:
        user = await ensure_user(message)
        await db.set_partner(user.user_id, None)
        await state.clear()
        await message.answer("Вышел из чата. Огонёк на месте 🔥", reply_markup=main_keyboard())

    async def show_friends(target: Message, user_id: int) -> None:
        friends = await db.list_friends(user_id)
        incoming = await db.list_incoming(user_id)
        now = today()
        for req in incoming:
            await target.answer(
                texts.incoming_request(req.friend.display_name()),
                reply_markup=request_keyboard(req.friend.user_id),
            )
        buttons = [
            (row.friend.user_id, texts.friend_button_title(row, user_id, now))
            for row in friends
        ]
        await target.answer(
            texts.friends_header(len(friends)),
            reply_markup=friends_keyboard(buttons),
        )

    async def send_ignite_notifications(
        bot: Bot,
        me: User,
        partner: User,
        streak: int,
    ) -> None:
        text = texts.ignited_text(streak, partner.first_name)
        other = texts.ignited_text(streak, me.first_name)
        await bot.send_message(me.user_id, text)
        try:
            await bot.send_message(partner.user_id, other)
        except TelegramForbiddenError:
            pass

    @router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
    async def cmd_start(
        message: Message,
        state: FSMContext,
        command: CommandObject,
        bot: Bot,
    ) -> None:
        me = await ensure_user(message)
        await state.clear()
        await db.set_partner(me.user_id, None)

        payload = (command.args or "").strip()
        if payload.startswith("u") and payload[1:].isdigit():
            other_id = int(payload[1:])
            if other_id == me.user_id:
                await message.answer("Это твоя же ссылка 🙂", reply_markup=main_keyboard())
            else:
                other = await db.get_user(other_id)
                if other is None:
                    await message.answer(
                        "Хозяин ссылки ещё не заходил в бота. Пусть нажмёт /start.",
                        reply_markup=main_keyboard(),
                    )
                else:
                    await db.accept_by_invite_link(other_id, me.user_id)
                    await message.answer(
                        texts.friend_added(other.display_name()),
                        reply_markup=main_keyboard(),
                    )
                    try:
                        await bot.send_message(
                            other_id,
                            texts.friend_added(me.display_name()),
                        )
                    except TelegramForbiddenError:
                        pass

        await message.answer(texts.WELCOME, reply_markup=main_keyboard())
        if config.bot_username:
            await message.answer(texts.invite_text(config.bot_username, me.user_id))

    @router.message(Command("help"), F.chat.type == ChatType.PRIVATE)
    @router.message(F.text == "ℹ️ Как это работает")
    async def cmd_help(message: Message) -> None:
        await ensure_user(message)
        await message.answer(texts.HOW_IT_WORKS, reply_markup=main_keyboard())

    @router.message(Command("friends"), F.chat.type == ChatType.PRIVATE)
    @router.message(F.text == "🔥 Мои огоньки")
    async def cmd_friends(message: Message, state: FSMContext) -> None:
        me = await ensure_user(message)
        current = await state.get_state()
        if current == Form.chatting.state:
            await leave_chat(message, state)
        await show_friends(message, me.user_id)

    @router.message(Command("top"), F.chat.type == ChatType.PRIVATE)
    @router.message(F.text == "🏆 Топ")
    async def cmd_top(message: Message) -> None:
        await ensure_user(message)
        items = await db.top_streaks()
        await message.answer(texts.top_text(items), reply_markup=main_keyboard())

    @router.message(Command("add"), F.chat.type == ChatType.PRIVATE)
    @router.message(F.text == "➕ Добавить друга")
    @router.callback_query(F.data == "add_friend")
    async def cmd_add(
        event: Message | CallbackQuery,
        state: FSMContext,
    ) -> None:
        if isinstance(event, CallbackQuery):
            message = event.message
            user = event.from_user
            await event.answer()
        else:
            message = event
            user = event.from_user
        if message is None or user is None:
            return
        await db.upsert_user(user.id, user.username, user.first_name or "")
        await state.set_state(Form.waiting_username)
        await message.answer(texts.ASK_USERNAME)
        if config.bot_username:
            await message.answer(texts.invite_text(config.bot_username, user.id))

    @router.message(Command("cancel"), F.chat.type == ChatType.PRIVATE)
    async def cmd_cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await db.set_partner(message.from_user.id, None)  # type: ignore[union-attr]
        await message.answer(texts.CANCELLED, reply_markup=main_keyboard())

    @router.message(F.text == "❌ Выйти из чата")
    async def cmd_exit_chat(message: Message, state: FSMContext) -> None:
        await leave_chat(message, state)

    @router.message(Form.waiting_username, F.chat.type == ChatType.PRIVATE)
    async def got_username(message: Message, state: FSMContext, bot: Bot) -> None:
        me = await ensure_user(message)
        other: User | None = None
        if message.user_shared:
            other = await db.get_user(message.user_shared.user_id)
            if other is None:
                await message.answer(
                    "Этот человек ещё не запускал бота. Скинь ему свою ссылку."
                )
                return
        elif message.contact and message.contact.user_id:
            other = await db.get_user(message.contact.user_id)
            if other is None:
                await message.answer(
                    "Этот человек ещё не запускал бота. Скинь ему свою ссылку."
                )
                return
        elif message.text:
            text = message.text.strip()
            if text.startswith("/"):
                await state.clear()
                await message.answer("Отменил ввод username.", reply_markup=main_keyboard())
                return
            other = await db.get_user_by_username(text)
            if other is None:
                await message.answer(
                    "Не нашёл такого пользователя. Он должен сначала нажать /start в боте.\n"
                    "Или скинь ему свою ссылку-приглашение."
                )
                if config.bot_username:
                    await message.answer(texts.invite_text(config.bot_username, me.user_id))
                return
        else:
            await message.answer("Пришли @username текстом.")
            return

        if other.user_id == me.user_id:
            await message.answer("С самим собой огонёк не копится 🙂")
            return

        existing = await db.get_friendship(me.user_id, other.user_id)
        if existing and existing.status == "accepted":
            await state.clear()
            row = FriendRow(friend=other, friendship=existing)
            await open_chat(
                user_id=me.user_id,
                partner=other,
                row=row,
                message=message,
                state=state,
            )
            return

        if existing and existing.status == "pending" and existing.invited_by == other.user_id:
            accepted = await db.accept_friendship(me.user_id, other.user_id)
            await state.clear()
            await message.answer(
                texts.friend_added(other.display_name()),
                reply_markup=main_keyboard(),
            )
            if accepted and accepted.status == "accepted":
                try:
                    await bot.send_message(
                        other.user_id,
                        texts.friend_added(me.display_name()),
                    )
                except TelegramForbiddenError:
                    pass
            return

        friendship = await db.create_invite(me.user_id, other.user_id)
        await state.clear()
        if friendship.status == "accepted":
            await message.answer(texts.friend_added(other.display_name()), reply_markup=main_keyboard())
            return
        await message.answer(texts.request_sent(other.display_name()), reply_markup=main_keyboard())
        try:
            await bot.send_message(
                other.user_id,
                texts.incoming_request(me.display_name()),
                reply_markup=request_keyboard(me.user_id),
            )
        except TelegramForbiddenError:
            await message.answer(
                "Не смог написать другу: пусть откроет бота и нажмёт /start."
            )

    @router.callback_query(FriendCB.filter())
    async def on_friend_cb(
        callback: CallbackQuery,
        callback_data: FriendCB,
        state: FSMContext,
        bot: Bot,
    ) -> None:
        me_user = callback.from_user
        if me_user is None or callback.message is None:
            await callback.answer()
            return
        me = await db.upsert_user(me_user.id, me_user.username, me_user.first_name or "")
        other = await db.get_user(callback_data.user_id)
        if other is None:
            await callback.answer("Пользователь не найден", show_alert=True)
            return

        if callback_data.action == "accept":
            friendship = await db.accept_friendship(me.user_id, other.user_id)
            if friendship is None or friendship.status != "accepted":
                await callback.answer("Заявку принять не получилось", show_alert=True)
                return
            await callback.message.answer(
                texts.friend_added(other.display_name()),
                reply_markup=main_keyboard(),
            )
            await callback.answer("Готово")
            try:
                await bot.send_message(
                    other.user_id,
                    texts.friend_added(me.display_name()),
                )
            except TelegramForbiddenError:
                pass
            return

        if callback_data.action == "decline":
            await db.decline_or_remove(me.user_id, other.user_id)
            await callback.message.answer("Заявку отклонил.")
            await callback.answer("Отклонено")
            return

        if callback_data.action == "remove":
            await db.decline_or_remove(me.user_id, other.user_id)
            await db.set_partner(me.user_id, None)
            await state.clear()
            await callback.message.answer(
                f"{other.first_name} удалён из друзей. Огонёк сброшен.",
                reply_markup=main_keyboard(),
            )
            await callback.answer("Удалено")
            return

        if callback_data.action == "open":
            friendship = await db.get_friendship(me.user_id, other.user_id)
            if friendship is None or friendship.status != "accepted":
                await callback.answer("Вы не друзья", show_alert=True)
                return
            row = FriendRow(friend=other, friendship=friendship)
            await open_chat(
                user_id=me.user_id,
                partner=other,
                row=row,
                callback=callback,
                state=state,
            )
            return

        await callback.answer()

    @router.message(Form.chatting, F.chat.type == ChatType.PRIVATE)
    async def relay_chat(message: Message, state: FSMContext, bot: Bot) -> None:
        me = await ensure_user(message)
        data = await state.get_data()
        partner_id = data.get("partner_id") or await db.get_partner(me.user_id)
        if not partner_id:
            await leave_chat(message, state)
            return
        if message.text in {
            "🔥 Мои огоньки",
            "➕ Добавить друга",
            "🏆 Топ",
            "ℹ️ Как это работает",
        }:
            return

        friendship = await db.get_friendship(me.user_id, partner_id)
        partner = await db.get_user(partner_id)
        if friendship is None or friendship.status != "accepted" or partner is None:
            await message.answer("Этот чат больше недоступен.", reply_markup=main_keyboard())
            await state.clear()
            await db.set_partner(me.user_id, None)
            return

        now = today()
        result = register_write(friendship.state, friendship.is_user_a(me.user_id), now)
        await db.save_state(friendship, result.state)
        friendship.state = result.state

        status = fire_status(result.state, friendship.is_user_a(me.user_id), now)
        header = texts.incoming_header(me, texts.status_label(status, result.state.streak))
        partner_in_chat = await db.get_partner(partner_id) == me.user_id
        reply_markup = None if partner_in_chat else incoming_message_keyboard(me.user_id)

        try:
            if result.expired and result.previous_streak > 0:
                gone = texts.expired_text(partner.first_name, result.previous_streak)
                other_gone = texts.expired_text(me.first_name, result.previous_streak)
                await message.answer(gone)
                try:
                    await bot.send_message(partner_id, other_gone)
                except TelegramForbiddenError:
                    pass

            if message.text and not message.via_bot:
                body = f"{header}\n\n{message.text}"
                await bot.send_message(partner_id, body, reply_markup=reply_markup)
            else:
                await bot.send_message(partner_id, header, reply_markup=reply_markup)
                await message.copy_to(partner_id)

            if result.ignited:
                await send_ignite_notifications(bot, me, partner, result.state.streak)
            else:
                await message.answer("✓ отправлено")
        except TelegramForbiddenError:
            await message.answer(
                "Друг ещё не открыл бота или заблокировал его. Пусть нажмёт /start."
            )

    @router.message(F.chat.type == ChatType.PRIVATE)
    async def fallback(message: Message, state: FSMContext) -> None:
        me = await ensure_user(message)
        partner_id = await db.get_partner(me.user_id)
        if partner_id:
            await state.set_state(Form.chatting)
            await state.update_data(partner_id=partner_id)
            await relay_chat(message, state, message.bot)
            return
        if message.text and message.text.startswith("@"):
            await state.set_state(Form.waiting_username)
            await got_username(message, state, message.bot)
            return
        await message.answer(
            "Выбери друга в «🔥 Мои огоньки» или добавь нового.",
            reply_markup=main_keyboard(),
        )

    return router


async def send_evening_reminders(bot: Bot, db: Database, tz: ZoneInfo) -> None:
    now = datetime.now(tz).date()
    pairs = await db.pairs_waiting_today(now)
    for friendship, user_a, user_b in pairs:
        for me, other in ((user_a, user_b), (user_b, user_a)):
            status = fire_status(friendship.state, friendship.is_user_a(me.user_id), now)
            try:
                await bot.send_message(
                    me.user_id,
                    texts.remind_text(other, friendship.state.streak, status),
                    reply_markup=remind_keyboard(other.user_id),
                )
            except TelegramForbiddenError:
                continue
