from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder


class FriendCB(CallbackData, prefix="fr"):
    action: str
    user_id: int


def main_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🔥 Мои огоньки"))
    builder.row(
        KeyboardButton(text="➕ Добавить друга"),
        KeyboardButton(text="🏆 Топ"),
    )
    builder.row(KeyboardButton(text="ℹ️ Как это работает"))
    return builder.as_markup(resize_keyboard=True)


def chat_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="❌ Выйти из чата"))
    builder.row(KeyboardButton(text="🔥 Мои огоньки"))
    return builder.as_markup(resize_keyboard=True)


def friends_keyboard(rows: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for user_id, title in rows:
        builder.row(
            InlineKeyboardButton(
                text=title,
                callback_data=FriendCB(action="open", user_id=user_id).pack(),
            )
        )
    builder.row(
        InlineKeyboardButton(text="➕ Добавить друга", callback_data="add_friend")
    )
    return builder.as_markup()


def request_keyboard(from_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Принять",
            callback_data=FriendCB(action="accept", user_id=from_id).pack(),
        ),
        InlineKeyboardButton(
            text="❌ Отклонить",
            callback_data=FriendCB(action="decline", user_id=from_id).pack(),
        ),
    )
    return builder.as_markup()


def incoming_message_keyboard(from_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="💬 Ответить",
            callback_data=FriendCB(action="open", user_id=from_id).pack(),
        )
    )
    return builder.as_markup()


def chat_actions_keyboard(partner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🗑 Удалить из друзей",
            callback_data=FriendCB(action="remove", user_id=partner_id).pack(),
        )
    )
    return builder.as_markup()


def remind_keyboard(partner_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🔥 Написать",
            callback_data=FriendCB(action="open", user_id=partner_id).pack(),
        )
    )
    return builder.as_markup()
